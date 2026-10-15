# Native Synology DSM package (initial test)

This package runs the Rust server and Angular browser interface directly on DSM,
without Container Manager, GTK, WebKitGTK, Xvfb or a desktop session. The first
target is **DS920+ (Intel x86_64), DSM 7.2.1-69057 Update 12**. Hardware testing
of revision 2 confirmed installation, start/stop, UI configuration, shared-folder
access, a small transfer and scheduled jobs. Revision 3's first-run provisioning
and upgrade from the bundled revision still need NAS verification, along with
NAS reboot persistence. Other models remain unverified.

## Build

The **Synology DSM test package** GitHub Actions workflow builds and
uploads an x86_64 SPK plus its SHA-256 checksum. It packages the current server
and frontend source; it does not publish a release or bundle an rclone executable.

Build the frontend with the repository's supported Node version. Build the
server on Linux with a musl toolchain, using a current stable Rust toolchain
and native prerequisites for any remaining C dependencies:

```sh
npm ci
npm run build
rustup target add x86_64-unknown-linux-musl
cd backend
cargo build -p rclone-manager-server --features vendored-dbus --release --target x86_64-unknown-linux-musl
cd ..
python3 synology/build_spk.py \
  --arch x86_64 \
  --binary backend/target/x86_64-unknown-linux-musl/release/rclone-manager-server \
  --output dist/synology/rclone-manager-0.3.4-1-x86_64.spk
```

The release server embeds the compiled frontend, translations and HTML
templates. Build the frontend before compiling it. The packager accepts a static
Linux ELF server executable of the selected CPU architecture and rejects macOS
binaries, dynamic Linux executables, wrong architectures and missing executables. It never substitutes a stub when the real server is unavailable.
Cross compiling also requires an appropriate C linker; installing a Rust target
alone does not supply one.

Rclone is installed through the application's existing first-run provisioning
flow. After signing in, choose **Install Rclone** to install it into persistent
application data, or select an existing compatible executable accessible to the
package account. Provisioning requires outbound internet access; manually
provided executables support offline setups. Subsequent application restarts and
SPK upgrades retain the provisioned binary and any custom executable selection.

When upgrading from revision 2, the former bundled executable is removed. If it
was selected, use the existing **Install Rclone** repair action to provision a
persistent replacement. Existing working custom executable paths are preserved.

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

5. Complete rclone setup using **Install Rclone** or an existing executable.
   Then grant the `rclone-manager` system internal user access only to the shared
   folders it should manage (DSM Control Panel → Shared Folder → Permissions →
   System internal user). Test listing a permitted folder and denial of an
   unpermitted folder.
6. Add a remote, list its files, copy a small test file and verify its contents.
   Test a scheduled transfer and confirm it still exists after stopping and
   starting the package and after restarting the NAS.
7. Install a rebuilt package with a higher numeric revision (`--version
0.3.4-2`). Confirm credentials, remotes, settings and schedules survive.

The service runs as its dedicated DSM package account, never root. It stores
settings, credentials, logs and cache in DSM's persistent package `var` directory,
not inside replaceable application files. The launcher uses PID identity checks
before sending SIGTERM, and reports a failed startup or graceful-stop timeout.
Upgrades copy the former `secrets.enc.json` credential store to `secrets.enc`
only if the new file is absent, retaining the original as a private backup.
Existing new-format stores are preserved.
Application self-updates are disabled so DSM Package Center owns the installed
server executable. Rclone provisioning and updates remain available.
The runtime log is `/var/packages/rclone-manager/var/logs/server.log`.

Keep the initial HTTP endpoint on a trusted LAN. Configure DSM's HTTPS reverse
proxy for encrypted access. DSM login credentials do not log into this service;
it has its own Basic authentication. To change the initial or existing password, stop the
package, replace `var/password` with a strong single-line password while retaining
mode `0600` and package ownership, then start it. The separate `var/secret` file protects the encrypted credential store; retain
it alongside the data and do not rotate it as an authentication password.
A separately enabled rcman vault retains its user-chosen password; the package
does not substitute its credential-store secret as the vault password. Back up the entire package
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
