"""Package integrity and DSM lifecycle regression tests (no NAS required)."""

import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import tarfile
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location(
    "build_spk", Path(__file__).parents[1] / "build_spk.py"
)
BUILDER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BUILDER)


def elf(machine=62, kind=1):
    data = bytearray(120)
    data[:6] = b"\x7fELF\x02\x01"
    struct.pack_into("<HH", data, 16, 2, machine)
    struct.pack_into("<Q", data, 32, 64)
    struct.pack_into("<HH", data, 54, 56, 1)
    struct.pack_into("<I", data, 64, kind)
    return data


SMOKE_SPEC = importlib.util.spec_from_file_location(
    "verify_running", Path(__file__).with_name("verify-running.py")
)
SMOKE = importlib.util.module_from_spec(SMOKE_SPEC)
SMOKE_SPEC.loader.exec_module(SMOKE)


class SmokePayloadTests(unittest.TestCase):
    def test_rcman_metadata_uses_flat_dotted_setting_identifier(self):
        metadata = {"options": {"developer.log_level": {"value": "debug"}}}
        self.assertEqual(SMOKE.log_level(metadata), "debug")

    def test_real_system_status_serialization_shape(self):
        status = {
            "status": "active",
            "pid": 1234,
            "rcloneInfo": {"version": "v1.75.0"},
            "stats": {},
            "memory": {},
            "hasActiveJobs": False,
        }
        self.assertEqual(SMOKE.engine_pid(status), 1234)
        for change in ({"status": "inactive"}, {"pid": None}, {"rcloneInfo": None}):
            with self.subTest(change=change), self.assertRaises(AssertionError):
                SMOKE.engine_pid(dict(status, **change))


class PackageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.binary = self.root / "server"
        self.binary.write_bytes(elf())
        self.browser = self.root / "browser"
        self.browser.mkdir()
        (self.browser / "index.html").write_text("<html>test</html>")
        self.i18n = self.root / "i18n"
        (self.i18n / "en-US").mkdir(parents=True)
        (self.i18n / "en-US/main.json").write_text("{}")

    def build(self, **kwargs):
        options = dict(
            binary=self.binary,
            rclone=self.binary,
            frontend=self.browser,
            translations=self.i18n,
            arch="x86_64",
            version="0.3.3-2",
            output=self.root / "test.spk",
        )
        options.update(kwargs)
        return BUILDER.build(**options)

    def test_archive_contains_real_payload_and_unprivileged_lifecycle(self):
        with tarfile.open(self.build()) as archive:
            info = archive.extractfile("INFO").read().decode()
            self.assertIn('arch="x86_64"', info)
            self.assertNotIn("ContainerManager", info)
            self.assertEqual(
                json.load(archive.extractfile("conf/privilege")),
                {"defaults": {"run-as": "package"}},
            )
            self.assertEqual(archive.getmember("scripts/start-stop-status").mode, 0o755)
            with tarfile.open(
                fileobj=io.BytesIO(archive.extractfile("package.tgz").read())
            ) as payload:
                self.assertEqual(
                    payload.extractfile("bin/rclone-manager-server").read(), elf()
                )
                self.assertEqual(
                    payload.getmember("bin/rclone-manager-server").mode, 0o755
                )
                self.assertIn("browser/index.html", payload.getnames())
                self.assertIn("i18n/en-US/main.json", payload.getnames())

    def test_rejects_wrong_machine_dynamic_and_non_elf(self):
        for data in (elf(machine=183), elf(kind=3), elf(kind=2), b"#!/bin/sh\nexit 0"):
            with self.subTest(data=data[:20]):
                self.binary.write_bytes(data)
                with self.assertRaises(ValueError):
                    self.build()

    def test_accepts_static_pie_but_rejects_shared_library_dependencies(self):
        data = elf(kind=2)
        struct.pack_into("<H", data, 16, 3)
        struct.pack_into("<Q", data, 72, 120)
        struct.pack_into("<Q", data, 96, 16)
        data.extend(bytes(16))  # DT_NULL: self-contained static PIE.
        self.binary.write_bytes(data)
        self.build()
        struct.pack_into("<q", data, 120, 1)  # DT_NEEDED: shared library.
        self.binary.write_bytes(data)
        with self.assertRaises(ValueError):
            self.build()

    def test_rejects_missing_frontend_invalid_version_and_symlinks(self):
        with self.assertRaises(ValueError):
            self.build(version='1.0"\narch="all')
        (self.browser / "outside").symlink_to(self.binary)
        with self.assertRaises(ValueError):
            self.build()
        (self.browser / "outside").unlink()
        (self.browser / "index.html").unlink()
        with self.assertRaises(ValueError):
            self.build()

    def test_credentials_private_and_preserved_on_reinstall(self):
        script = BUILDER.ROOT / "scripts/postinst"
        env = dict(os.environ, SYNOPKG_PKGVAR=str(self.root / "persistent data"))
        subprocess.run(["sh", str(script)], env=env, check=True)
        password = Path(env["SYNOPKG_PKGVAR"]) / "password"
        first = password.read_text()
        self.assertEqual(first, "admin")
        self.assertEqual(password.stat().st_mode & 0o777, 0o600)
        password.write_text("custom password !$ characters")
        subprocess.run(["sh", str(script)], env=env, check=True)
        self.assertEqual(password.read_text(), "custom password !$ characters")
        password.write_text("")
        self.assertNotEqual(subprocess.run(["sh", str(script)], env=env).returncode, 0)

    def test_failed_start_is_reported_and_preserves_credentials(self):
        destination = self.root / "package files"
        (destination / "bin").mkdir(parents=True)
        server = destination / "bin/rclone-manager-server"
        server.write_text("#!/bin/sh\nexit 1\n")
        server.chmod(0o755)
        data = self.root / "persistent data"
        env = dict(
            os.environ, SYNOPKG_PKGVAR=str(data), SYNOPKG_PKGDEST=str(destination)
        )
        script = BUILDER.ROOT / "scripts/start-stop-status"
        result = subprocess.run(
            ["sh", str(script), "start"], env=env, capture_output=True
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"Server failed to start", result.stderr)
        self.assertFalse((data / "server.pid").exists())
        self.assertEqual((data / "password").read_text(), "admin")
        self.assertRegex((data / "secret").read_text(), r"^[0-9a-f]{64}$")
        self.assertNotEqual(
            (data / "secret").read_text(), (data / "password").read_text()
        )
        self.assertEqual((data / "secret").stat().st_mode & 0o777, 0o600)
        secret = (data / "secret").read_bytes()
        (data / "password").write_text("custom start password !$")
        result = subprocess.run(
            ["sh", str(script), "start"], env=env, capture_output=True
        )
        self.assertEqual(result.returncode, 1)
        self.assertEqual((data / "password").read_text(), "custom start password !$")
        self.assertEqual((data / "secret").read_bytes(), secret)

    def test_stale_pid_cannot_signal_unrelated_process(self):
        env = dict(
            os.environ, SYNOPKG_PKGVAR=str(self.root), SYNOPKG_PKGDEST=str(self.root)
        )
        (self.root / "server.pid").write_text(str(os.getpid()))
        script = BUILDER.ROOT / "scripts/start-stop-status"
        self.assertEqual(
            subprocess.run(
                ["sh", str(script), "status"], env=env, capture_output=True
            ).returncode,
            3,
        )
        subprocess.run(
            ["sh", str(script), "stop"], env=env, check=True, capture_output=True
        )
        self.assertFalse((self.root / "server.pid").exists())


if __name__ == "__main__":
    unittest.main()
