#!/usr/bin/env python3
"""Verify first-run application provisioning and persistent engine across restarts."""
import base64
import json
import os
from pathlib import Path
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


def engine_pid(status):
    """Validate the camelCase status payload serialized by SystemStatusPayload."""
    assert status["status"] == "active" and status["rcloneInfo"], status
    pid = status["pid"]
    assert isinstance(pid, int) and not isinstance(pid, bool) and pid > 1, status
    return pid


def log_level(settings):
    """rcman metadata indexes each setting by its flat dotted identifier."""
    return settings["options"]["developer.log_level"]["value"]


def installed_binary(settings, data_dir):
    """Require provisioning to persist the full binary path in package data."""
    value = settings["options"]["core.rclone_binary"]["value"]
    assert isinstance(value, str) and value, value
    path = Path(value)
    assert (
        path.is_absolute() and path.resolve() == (data_dir / "rclone").resolve()
    ), value
    return path


def main():
    root = Path(sys.argv[1])
    phase = sys.argv[2]
    data_dir = Path(os.environ["SYNOPKG_PKGVAR"])
    password = (data_dir / "password").read_text().strip()
    authorization = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()

    def invoke(command, args=None, timeout=10):
        request = Request(
            "http://127.0.0.1:8080/api/invoke",
            data=json.dumps({"command": command, "args": args or {}}).encode(),
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=timeout) as response:
            body = json.load(response)
        if not body.get("success"):
            raise RuntimeError(body)
        return body["data"]

    if phase == "before-restart":
        assert not (data_dir / "rclone").exists(), "First run already has rclone"
        status = invoke("get_system_status_snapshot")
        assert status["status"] != "active", status
        # Use exactly the browser's existing command: download, checksum, extraction,
        # settings update and engine restart all run inside the actual application.
        invoke("provision_rclone", {"path": None}, timeout=300)

    for attempt in range(120):
        try:
            status = invoke("get_system_status_snapshot")
            if status["status"] == "active":
                break
        except (URLError, TimeoutError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError(
            "Application-provisioned rclone engine did not become active"
        )

    pid = engine_pid(status)
    settings = invoke("load_settings")
    expected_binary = installed_binary(settings, data_dir)
    assert expected_binary.is_file() and os.access(expected_binary, os.X_OK)
    assert Path(f"/proc/{pid}/exe").resolve() == expected_binary.resolve(), status
    (root / "engine.pid").write_text(str(pid))
    assert invoke(
        "get_remote_types"
    ), "Expected provider definitions from real rclone RC"
    assert (data_dir / "config/rclone.conf").read_bytes() == (
        root / "saved-rclone.conf"
    ).read_bytes(), "Existing rclone configuration changed"

    if phase == "before-restart":
        baseline = invoke("load_settings")
        invoke(
            "save_setting",
            {"category": "developer", "key": "log_level", "value": "debug"},
        )
        saved = invoke("load_settings")
        assert baseline != saved, "Settings write did not change persisted value"
        assert log_level(saved) == "debug"
        (root / "saved-settings.json").write_text(json.dumps(saved, sort_keys=True))
        (root / "saved-password").write_text(password)
        (root / "saved-secret").write_bytes((data_dir / "secret").read_bytes())
        (root / "saved-binary").write_bytes(expected_binary.read_bytes())
    else:
        assert invoke("load_settings") == json.loads(
            (root / "saved-settings.json").read_text()
        )
        assert password == (root / "saved-password").read_text()
        assert (data_dir / "secret").read_bytes() == (
            root / "saved-secret"
        ).read_bytes()
        assert expected_binary.read_bytes() == (root / "saved-binary").read_bytes()


if __name__ == "__main__":
    main()
