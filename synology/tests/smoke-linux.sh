#!/bin/sh
# Exercise the actual packaged Linux server, not a substitute process.
set -eu
: "${1:?Usage: smoke-linux.sh package.spk}"
[ "$(uname -s)" = Linux ] && [ "$(uname -m)" = x86_64 ] || {
    echo 'This smoke test requires a native x86_64 Linux runner' >&2
    exit 1
}
if command -v rclone >/dev/null 2>&1; then
    echo 'Use a clean runner without an existing rclone in PATH to test first-run provisioning' >&2
    exit 1
fi
test_dir=$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)
smoke_root=$(mktemp -d)
cleanup() {
    result=$?
    if [ "$result" -ne 0 ] && [ -f "$SYNOPKG_PKGVAR/logs/server.log" ]; then
        cat "$SYNOPKG_PKGVAR/logs/server.log" >&2
    fi
    if [ -f "$smoke_root/scripts/start-stop-status" ]; then
        sh "$smoke_root/scripts/start-stop-status" stop || true
    fi
    rm -rf "$smoke_root"
}
trap cleanup EXIT HUP INT TERM
export SYNOPKG_PKGDEST="$smoke_root/target"
export SYNOPKG_PKGVAR="$smoke_root/var"
# DSM exposes package files through /var/packages/.../target symlinks.
mkdir -p "$smoke_root/installed" "$SYNOPKG_PKGVAR"
ln -s "$smoke_root/installed" "$SYNOPKG_PKGDEST"
tar -xf "$1" -C "$smoke_root"
tar -xzf "$smoke_root/package.tgz" -C "$SYNOPKG_PKGDEST"
[ ! -e "$SYNOPKG_PKGDEST/bin/rclone" ] || {
    echo 'Package must not bundle rclone; exercise the application provision command' >&2
    exit 1
}
sh "$smoke_root/scripts/postinst"
mkdir -p "$SYNOPKG_PKGVAR/config"
printf '[smoke-local]\ntype = local\n' > "$SYNOPKG_PKGVAR/config/rclone.conf"
cp "$SYNOPKG_PKGVAR/config/rclone.conf" "$smoke_root/saved-rclone.conf"
sh "$smoke_root/scripts/start-stop-status" start
sh "$smoke_root/scripts/start-stop-status" status
attempt=0
until curl --fail --silent http://127.0.0.1:8080/health > /dev/null; do
    attempt=$((attempt + 1))
    if [ "$attempt" -ge 60 ]; then
        cat "$SYNOPKG_PKGVAR/logs/server.log" >&2
        exit 1
    fi
    sleep 1
done
password=$(cat "$SYNOPKG_PKGVAR/password")
# Basic authentication must protect RPC access, and Angular must be served.
code=$(curl --silent --output /dev/null --write-out '%{http_code}' http://127.0.0.1:8080/api/events)
[ "$code" = 401 ] || { echo "Expected unauthenticated API 401, got $code" >&2; exit 1; }
curl --fail --silent --request POST --user "admin:$password" http://127.0.0.1:8080/api/auth/session > /dev/null
curl --fail --silent http://127.0.0.1:8080/ > "$smoke_root/index.html"
grep -q '<app-root' "$smoke_root/index.html"
curl --fail --silent http://127.0.0.1:8080/assets/i18n/en-US/main.json > "$smoke_root/translation.json"
python3 -c 'import json,sys; assert json.load(open(sys.argv[1]))' "$smoke_root/translation.json"
python3 "$test_dir/verify-running.py" "$smoke_root" before-restart
sh "$smoke_root/scripts/start-stop-status" stop
engine_pid=$(cat "$smoke_root/engine.pid")
if kill -0 "$engine_pid" 2>/dev/null; then
    echo 'Provisioned rclone child survived server shutdown' >&2
    exit 1
fi
# The install hook runs again on upgrade and must preserve user data and secrets.
sh "$smoke_root/scripts/postinst"
cmp "$SYNOPKG_PKGVAR/config/rclone.conf" "$smoke_root/saved-rclone.conf"
sh "$smoke_root/scripts/start-stop-status" start
python3 "$test_dir/verify-running.py" "$smoke_root" after-restart
sh "$smoke_root/scripts/start-stop-status" stop
engine_pid=$(cat "$smoke_root/engine.pid")
if kill -0 "$engine_pid" 2>/dev/null; then
    echo 'Provisioned rclone child survived restarted server shutdown' >&2
    exit 1
fi
if sh "$smoke_root/scripts/start-stop-status" status; then
    echo 'Server still reports running after stop' >&2
    exit 1
fi
