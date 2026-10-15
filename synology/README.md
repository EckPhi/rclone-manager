# Native Synology DSM package (initial test)

This package runs the Rust server and Angular browser interface directly on DSM,
without Container Manager, GTK, WebKitGTK, Xvfb or a desktop session. The first
target is **DS920+ (Intel x86_64), DSM 7.2.1-69057 Update 12**. Installation on a
physical NAS remains an explicit acceptance test; automated archive tests cannot
prove DSM installation or shared-folder permissions.

## Build

The manual **Synology DSM test package** GitHub Actions workflow builds and
uploads an x86_64 SPK plus its SHA-256 checksum. It verifies the pinned official
rclone download and packages the current source; it does not publish a release.

Build the frontend with the repository's supported Node version. Build the
server on Linux with a musl toolchain, using a current stable Rust toolchain
and native prerequisites for any remaining C dependencies:

```sh
npm ci
npm run build
rustup target add x86_64-unknown-linux-musl
cd src-tauri
cargo build --release --target x86_64-unknown-linux-musl --no-default-features --features native-headless
cd ..
python3 synology/build_spk.py \
  --arch x86_64 \
  --binary src-tauri/target/x86_64-unknown-linux-musl/release/rclone-manager \
  --rclone /absolute/path/to/linux-amd64/rclone \
  --output dist/synology/rclone-manager-0.3.3-2-x86_64.spk
```

Use the official Linux amd64 rclone executable, **version 1.75.0 or newer**,
verified against the upstream download checksum. The packager requires both
executables to be static Linux ELF files of the selected CPU architecture, a
compiled frontend and English translations. It rejects macOS binaries, dynamic
Linux executables, wrong architectures and missing assets. It never substitutes
a stub when the real server is unavailable. Cross compiling also requires an
appropriate C linker; installing a Rust target alone does not supply one.

`--arch aarch64` maps to DSM's `armv8` family but is not yet NAS tested. ARM32 and
other DSM architectures are outside this initial package.

## Install and test on the DS920+

1. Back up any existing RClone Manager settings and rclone configuration.
2. In DSM Package Center, choose **Manual Install**, select the `.spk`, and
   acknowledge DSM's third-party package notice.
3. Start RClone Manager. Use Package Center's **Open** link or visit
   `http://NAS-IP:8080/`. Port 8080 must be free; allow it in the DSM firewall on
   your trusted LAN if necessary.
4. Sign in with username **`admin`** and password **`admin`** on a fresh install.
   Change this initial password before exposing the service beyond your trusted LAN.
   Upgrades preserve any existing password.

5. Grant the `rclone-manager` system internal user access only to the shared
   folders it should manage (DSM Control Panel → Shared Folder → Permissions →
   System internal user). Test listing a permitted folder and denial of an
   unpermitted folder.
6. Add a remote, list its files, copy a small test file and verify its contents.
   Test a scheduled transfer and confirm it still exists after stopping and
   starting the package and after restarting the NAS.
7. Install a rebuilt package with a higher numeric revision (`--version
0.3.3-3`). Confirm credentials, remotes, settings and schedules survive.

The service runs as its dedicated DSM package account, never root. It stores
settings, credentials, logs and cache in DSM's persistent package `var` directory,
not inside replaceable application files. The launcher uses PID identity checks
before sending SIGTERM, and reports a failed startup or graceful-stop timeout.
The runtime log is `/var/packages/rclone-manager/var/logs/server.log`.

Keep the initial HTTP endpoint on a trusted LAN. Configure DSM's HTTPS reverse
proxy for encrypted access. DSM login credentials do not log into this service;
it has its own Basic authentication. To change the initial or existing password, stop the
package, replace `var/password` with a strong single-line password while retaining
mode `0600` and package ownership, then start it. The separate `var/secret` file protects the encrypted credential store; retain
it alongside the data and do not rotate it as an authentication password. Back up the entire package
`var` directory before uninstalling; DSM may remove package data on uninstall.

Mount/FUSE behavior and other privileged operations are not accepted for this
first package. Normal file browsing and transfers use the permissions of the
package account. Installing this package does not alter `/etc/fuse.conf` or grant
root access.

## Automated verification

```sh
python3 -m unittest discover -s synology/tests -v
sh -n synology/scripts/start-stop-status synology/scripts/postinst
```

Tests validate ELF rejection, archive layout and executable permissions,
unprivileged execution configuration, persistent private credentials, invalid
inputs and stale-PID safety. They use synthetic ELF headers solely as unit test
fixtures; a distributable SPK must use actual compiled executables.

Run `synology/tests/smoke-linux.sh` against the built SPK on native x86_64 Linux
to verify the installed lifecycle, browser, authentication, engine and restart
persistence. The Synology workflow runs this check. On Apple Silicon, Rosetta
reports `/proc/<pid>/exe` as the emulator instead of the server, so the launcher's
strict process identity check rejects that emulated environment; use native
x86_64 Linux or the NAS for the complete lifecycle test.

Packaging follows Synology's official [DSM package guide](https://help.synology.com/developer-guide/),
[privilege configuration](https://help.synology.com/developer-guide/privilege/privilege_config.html),
[package scripts](https://help.synology.com/developer-guide/synology_package/scripts.html),
[INFO fields](https://help.synology.com/developer-guide/synology_package/INFO_optional_fields.html)
and [architecture family mapping](https://help.synology.com/developer-guide/appendix/platarchs.html).
