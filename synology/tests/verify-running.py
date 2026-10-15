#!/usr/bin/env python3
"""Verify real authenticated RPC, bundled engine and settings across restarts."""
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


def main():
    root = Path(sys.argv[1])
    phase = sys.argv[2]
    data_dir = Path(os.environ["SYNOPKG_PKGVAR"])
    password = (data_dir / "password").read_text().strip()
    authorization = "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()

    def invoke(command, args=None):
        request = Request(
            "http://127.0.0.1:8080/api/invoke",
            data=json.dumps({"command": command, "args": args or {}}).encode(),
            headers={
                "Authorization": authorization,
                "Content-Type": "application/json",
            },
        )
        with urlopen(request, timeout=10) as response:
            body = json.load(response)
        if not body.get("success"):
            raise RuntimeError(body)
        return body["data"]

    for attempt in range(60):
        try:
            status = invoke("get_system_status_snapshot")
            if status["status"] == "active":
                break
        except (URLError, TimeoutError):
            pass
        time.sleep(1)
    else:
        raise RuntimeError("Bundled rclone engine did not become active")

    pid = engine_pid(status)
    expected_binary = Path(os.environ["SYNOPKG_PKGDEST"]) / "bin/rclone"
    assert Path(f"/proc/{pid}/exe").resolve() == expected_binary.resolve(), status
    (root / "engine.pid").write_text(str(pid))
    assert invoke(
        "get_remote_types"
    ), "Expected provider definitions from real rclone RC"

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
    else:
        assert invoke("load_settings") == json.loads(
            (root / "saved-settings.json").read_text()
        )
        assert password == (root / "saved-password").read_text()
        assert (data_dir / "secret").read_bytes() == (
            root / "saved-secret"
        ).read_bytes()


if __name__ == "__main__":
    main()
