#!/usr/bin/env python3
"""Build a DSM 7 native package from a real, statically linked Linux server."""

import argparse
import gzip
import hashlib
import io
import json
from pathlib import Path
import re
import struct
import tarfile

ROOT = Path(__file__).resolve().parent
ARCHES = {"x86_64": 62, "aarch64": 183}


def validate_elf(binary: Path, arch: str) -> None:
    """Reject host executables, wrong CPU, dynamic loaders and shared libraries."""
    data = binary.read_bytes()
    if len(data) < 64 or data[:6] != b"\x7fELF\x02\x01":
        raise ValueError("Server must be a 64-bit little-endian Linux ELF executable")
    if struct.unpack_from("<H", data, 18)[0] != ARCHES[arch]:
        raise ValueError(f"Server ELF machine does not match {arch}")
    if struct.unpack_from("<H", data, 16)[0] not in (2, 3):
        raise ValueError("Server must be executable ELF")
    offset = struct.unpack_from("<Q", data, 32)[0]
    size, count = struct.unpack_from("<HH", data, 54)
    if not count or size < 56 or offset + count * size > len(data):
        raise ValueError("Invalid or truncated ELF program headers")
    for index in range(count):
        kind = struct.unpack_from("<I", data, offset + index * size)[0]
        if kind == 3:  # PT_INTERP requires a dynamic loader.
            raise ValueError("Server must be statically linked (build for Linux musl)")
        if kind == 2:  # Static PIE may have relocations but never DT_NEEDED.
            header = offset + index * size
            start = struct.unpack_from("<Q", data, header + 8)[0]
            length = struct.unpack_from("<Q", data, header + 32)[0]
            if not length or length % 16 or start + length > len(data):
                raise ValueError("Invalid ELF dynamic segment")
            for entry in range(start, start + length, 16):
                tag = struct.unpack_from("<q", data, entry)[0]
                if tag == 0:
                    break
                if tag == 1:
                    raise ValueError(
                        "Server requires shared libraries; build for Linux musl"
                    )


def add_bytes(archive: tarfile.TarFile, name: str, content: bytes, mode=0o644):
    info = tarfile.TarInfo(name)
    info.size = len(content)
    info.mode = mode
    info.mtime = 0
    archive.addfile(info, io.BytesIO(content))


def build(
    binary: Path,
    arch: str,
    version: str,
    output: Path,
):
    if not re.fullmatch(r"\d+(?:\.\d+){1,3}-\d+", version):
        raise ValueError("DSM version must be numeric, e.g. 0.3.4-1")
    validate_elf(binary, arch)
    payload = io.BytesIO()
    with gzip.GzipFile(fileobj=payload, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w") as archive:
            add_bytes(archive, "bin/rclone-manager-server", binary.read_bytes(), 0o755)
    packed = payload.getvalue()
    fields = {
        "package": "rclone-manager",
        "displayname": "RClone Manager",
        "version": version,
        "os_min_ver": "7.0-40000",
        "arch": "armv8" if arch == "aarch64" else arch,
        "description": "Native RClone Manager web interface. Initial test package.",
        "maintainer": "RClone Manager contributors",
        "beta": "yes",
        "adminprotocol": "http",
        "adminport": "8080",
        "adminurl": "",
        "checkport": "yes",
        "ctl_stop": "yes",
        "silent_upgrade": "yes",
        "checksum": hashlib.md5(packed, usedforsecurity=False).hexdigest(),
    }
    info = "".join(f'{key}="{value}"\n' for key, value in fields.items()).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(output, "w", format=tarfile.USTAR_FORMAT) as archive:
        add_bytes(archive, "INFO", info)
        add_bytes(archive, "package.tgz", packed)
        add_bytes(
            archive,
            "conf/privilege",
            json.dumps({"defaults": {"run-as": "package"}}).encode(),
        )
        for name in ("start-stop-status", "postinst"):
            add_bytes(
                archive,
                f"scripts/{name}",
                (ROOT / "scripts" / name).read_bytes(),
                0o755,
            )
        for name in ("preinst", "preuninst", "postuninst", "preupgrade", "postupgrade"):
            add_bytes(archive, f"scripts/{name}", b"#!/bin/sh\nexit 0\n", 0o755)
        add_bytes(
            archive,
            "PACKAGE_ICON.PNG",
            (ROOT.parent / "backend/desktop/icons/64x64.png").read_bytes(),
        )
        add_bytes(
            archive,
            "PACKAGE_ICON_256.PNG",
            (ROOT.parent / "backend/desktop/icons/128x128@2x.png").read_bytes(),
        )
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", type=Path, required=True)
    parser.add_argument("--arch", choices=ARCHES, required=True)
    parser.add_argument("--version", default="0.3.4-1")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(
            build(
                args.binary,
                args.arch,
                args.version,
                args.output,
            )
        )
    except (ValueError, OSError) as error:
        parser.exit(1, f"Cannot build SPK: {error}\n")


if __name__ == "__main__":
    main()
