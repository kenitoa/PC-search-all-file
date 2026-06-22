"""Check whether expected PC drives are visible before full-drive search.

This module verifies the question that matters before search starts: is a drive
root visible to the filesystem, and does Windows report it as a logical drive?
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Mapping, Sequence


DRIVE_UNKNOWN = 0
DRIVE_NO_ROOT_DIR = 1
DRIVE_REMOVABLE = 2
DRIVE_FIXED = 3
DRIVE_REMOTE = 4
DRIVE_CDROM = 5
DRIVE_RAMDISK = 6

DRIVE_TYPE_NAMES = {
    DRIVE_UNKNOWN: "unknown",
    DRIVE_NO_ROOT_DIR: "missing_root",
    DRIVE_REMOVABLE: "removable",
    DRIVE_FIXED: "fixed",
    DRIVE_REMOTE: "remote",
    DRIVE_CDROM: "cdrom",
    DRIVE_RAMDISK: "ramdisk",
}


@dataclass(frozen=True)
class DriveStatus:
    root: str
    filesystem_exists: bool
    windows_logical: bool
    drive_type: int | None
    drive_type_name: str
    searchable_by_default: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class DiskCheckReport:
    expected_roots: tuple[str, ...]
    filesystem_roots: tuple[str, ...]
    windows_logical_roots: tuple[str, ...]
    statuses: tuple[DriveStatus, ...]

    @property
    def searchable_roots(self) -> tuple[str, ...]:
        return tuple(status.root for status in self.statuses if status.searchable_by_default)

    @property
    def missing_expected_roots(self) -> tuple[str, ...]:
        return tuple(
            status.root
            for status in self.statuses
            if not status.filesystem_exists and not status.windows_logical
        )

    @property
    def blocked_expected_roots(self) -> tuple[str, ...]:
        return tuple(
            status.root
            for status in self.statuses
            if (status.filesystem_exists or status.windows_logical)
            and not status.searchable_by_default
        )

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["searchable_roots"] = self.searchable_roots
        data["missing_expected_roots"] = self.missing_expected_roots
        data["blocked_expected_roots"] = self.blocked_expected_roots
        return data


def check_disks(
    expected_roots: Sequence[str | Path] = ("C:\\", "D:\\"),
    *,
    filesystem_exists: Callable[[str], bool] | None = None,
    logical_drive_provider: Callable[[], Mapping[str, int]] | None = None,
) -> DiskCheckReport:
    """Compare expected drive roots with filesystem and Windows logical-drive visibility."""
    exists = filesystem_exists or _filesystem_root_exists
    logical_provider = logical_drive_provider or _windows_logical_drive_types
    logical_drives = dict(logical_provider())
    normalized_expected = tuple(_normalize_drive_root(root) for root in expected_roots)
    all_roots = _unique_roots((*normalized_expected, *logical_drives.keys()))

    filesystem_roots = tuple(root for root in all_roots if exists(root))
    windows_logical_roots = tuple(root for root in all_roots if root in logical_drives)
    statuses = tuple(
        _build_status(
            root,
            filesystem_exists=exists(root),
            drive_type=logical_drives.get(root),
        )
        for root in normalized_expected
    )
    return DiskCheckReport(
        normalized_expected,
        filesystem_roots,
        windows_logical_roots,
        statuses,
    )


def _build_status(root: str, *, filesystem_exists: bool, drive_type: int | None) -> DriveStatus:
    windows_logical = drive_type is not None
    drive_type_name = DRIVE_TYPE_NAMES.get(drive_type, "not_reported") if drive_type is not None else "not_reported"
    searchable = filesystem_exists and drive_type == DRIVE_FIXED

    if searchable:
        reason = "visible fixed drive"
    elif not filesystem_exists and not windows_logical:
        reason = "not visible to filesystem or Windows logical-drive list"
    elif not filesystem_exists:
        reason = "reported by Windows but root is not accessible"
    elif drive_type != DRIVE_FIXED:
        reason = f"visible but not a fixed local drive: {drive_type_name}"
    else:
        reason = "not searchable by default"

    return DriveStatus(
        root,
        filesystem_exists,
        windows_logical,
        drive_type,
        drive_type_name,
        searchable,
        reason,
    )


def _windows_logical_drive_types() -> dict[str, int]:
    if os.name != "nt":
        return {os.fspath(Path("/")): DRIVE_FIXED}

    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(254)
    length = kernel32.GetLogicalDriveStringsW(len(buffer), buffer)
    if length == 0:
        return {}

    drives = [drive for drive in buffer.value.split("\x00") if drive]
    return {
        _normalize_drive_root(drive): int(kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)))
        for drive in drives
    }


def _filesystem_root_exists(root: str) -> bool:
    return Path(root).exists()


def _normalize_drive_root(root: str | Path) -> str:
    value = os.fspath(root).strip()
    if os.name == "nt":
        value = value.replace("/", "\\")
        if len(value) == 1 and value.isalpha():
            value = f"{value}:\\"
        elif len(value) == 2 and value[1] == ":":
            value = f"{value}\\"
        return value[0].upper() + value[1:] if value else value
    return os.fspath(Path(value or "/"))


def _unique_roots(roots: Sequence[str]) -> tuple[str, ...]:
    seen: set[str] = set()
    unique: list[str] = []
    for root in roots:
        key = os.path.normcase(root)
        if key not in seen:
            seen.add(key)
            unique.append(root)
    return tuple(unique)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check C/D drive visibility for full-PC search.")
    parser.add_argument(
        "expected_roots",
        nargs="*",
        default=("C:\\", "D:\\"),
        help="Expected drive roots to verify. Defaults to C:\\ and D:\\.",
    )
    parser.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    args = parser.parse_args(argv)

    report = check_disks(args.expected_roots)
    if args.json:
        print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
    else:
        for status in report.statuses:
            marker = "OK" if status.searchable_by_default else "WARN"
            print(f"{marker} {status.root} {status.reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
