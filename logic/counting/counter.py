"""Count files across local PC storage.

The module keeps the responsibility of this folder narrow: discover accessible
local roots, walk them without following links, and return auditable counts.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Sequence


DRIVE_FIXED = 3
COUNT_FILES = "files"
COUNT_DIRECTORIES = "directories"
COUNT_BOTH = "both"
WINDOWS_SETUP_ROOT_NAMES = frozenset(
    {
        "$Recycle.Bin",
        "$WinREAgent",
        "Config.Msi",
        "Documents and Settings",
        "PerfLogs",
        "Program Files",
        "Program Files (x86)",
        "ProgramData",
        "Recovery",
        "System Volume Information",
        "Windows",
    }
)


@dataclass(frozen=True)
class ScanError:
    path: str
    error: str


@dataclass(frozen=True)
class RootCount:
    root: str
    files: int
    directories_scanned: int
    directories_counted: int
    excluded_windows_setup_directories: tuple[str, ...] = ()
    errors: tuple[ScanError, ...] = ()


@dataclass(frozen=True)
class CountResult:
    roots: tuple[RootCount, ...]

    @property
    def total_files(self) -> int:
        return sum(root.files for root in self.roots)

    @property
    def directories_scanned(self) -> int:
        return sum(root.directories_scanned for root in self.roots)

    @property
    def directories_counted(self) -> int:
        return sum(root.directories_counted for root in self.roots)

    @property
    def excluded_windows_setup_directories(self) -> tuple[str, ...]:
        excluded: list[str] = []
        for root in self.roots:
            excluded.extend(root.excluded_windows_setup_directories)
        return tuple(excluded)

    @property
    def errors(self) -> tuple[ScanError, ...]:
        merged: list[ScanError] = []
        for root in self.roots:
            merged.extend(root.errors)
        return tuple(merged)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["total_files"] = self.total_files
        data["directories_scanned"] = self.directories_scanned
        data["directories_counted"] = self.directories_counted
        data["windows_setup_excluded_count"] = len(self.excluded_windows_setup_directories)
        data["error_count"] = len(self.errors)
        return data


def discover_local_roots() -> tuple[Path, ...]:
    """Return local fixed-drive roots on Windows, or filesystem root elsewhere."""
    if os.name != "nt":
        return (Path("/"),)

    kernel32 = ctypes.windll.kernel32
    buffer = ctypes.create_unicode_buffer(254)
    length = kernel32.GetLogicalDriveStringsW(len(buffer), buffer)
    if length == 0:
        anchor = Path.cwd().anchor
        return (Path(anchor),) if anchor else (Path.cwd(),)

    drives = [drive for drive in buffer.value.split("\x00") if drive]
    fixed_drives = [
        Path(drive)
        for drive in drives
        if kernel32.GetDriveTypeW(ctypes.c_wchar_p(drive)) == DRIVE_FIXED
    ]
    return tuple(fixed_drives)


def count_files(
    roots: Sequence[Path | str] | None = None,
    *,
    exclude_windows_setup: bool = True,
    count_mode: str = COUNT_FILES,
) -> CountResult:
    """Count files under roots.

    The walk does not follow directory links, which avoids cycles and keeps a
    count tied to entries physically reachable from the selected roots.
    """
    if count_mode not in {COUNT_FILES, COUNT_DIRECTORIES, COUNT_BOTH}:
        raise ValueError(f"unsupported count mode: {count_mode}")

    selected_roots = tuple(Path(root) for root in roots) if roots else discover_local_roots()
    return CountResult(
        tuple(_count_root(root, exclude_windows_setup, count_mode) for root in selected_roots)
    )


def _count_root(root: Path, exclude_windows_setup: bool, count_mode: str) -> RootCount:
    files = 0
    directories_scanned = 0
    directories_counted = 0
    excluded_windows_setup_directories: list[str] = []
    errors: list[ScanError] = []
    stack = [root]

    while stack:
        current = stack.pop()
        if exclude_windows_setup and _is_windows_setup_directory(root, current):
            excluded_windows_setup_directories.append(str(current))
            continue

        try:
            with os.scandir(current) as entries:
                directories_scanned += 1
                directories_counted += 1
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            child = Path(entry.path)
                            if exclude_windows_setup and _is_windows_setup_directory(root, child):
                                excluded_windows_setup_directories.append(str(child))
                            else:
                                stack.append(child)
                        elif count_mode in {COUNT_FILES, COUNT_BOTH} and entry.is_file(
                            follow_symlinks=False
                        ):
                            files += 1
                    except OSError as exc:
                        errors.append(ScanError(entry.path, _format_error(exc)))
        except OSError as exc:
            errors.append(ScanError(str(current), _format_error(exc)))

    return RootCount(
        str(root),
        files,
        directories_scanned,
        directories_counted,
        tuple(excluded_windows_setup_directories),
        tuple(errors),
    )


def _is_windows_setup_directory(root: Path, current: Path) -> bool:
    if os.name != "nt":
        return False

    resolved_root = Path(os.path.abspath(os.fspath(root)))
    resolved_current = Path(os.path.abspath(os.fspath(current)))
    root_key = os.path.normcase(os.fspath(resolved_root))
    current_key = os.path.normcase(os.fspath(resolved_current))
    try:
        relative_parts = Path(current_key).relative_to(Path(root_key)).parts
    except ValueError:
        relative_parts = resolved_current.parts

    if resolved_current.name in WINDOWS_SETUP_ROOT_NAMES and resolved_current.parent == Path(
        resolved_current.anchor
    ):
        return True

    if not relative_parts:
        return False

    # Exclude Windows-created setup/system folders at a drive root, not user
    # folders that merely contain a child with the same name.
    if resolved_root.anchor and resolved_root == Path(resolved_root.anchor):
        return relative_parts[0] in WINDOWS_SETUP_ROOT_NAMES

    return False


def _format_error(exc: OSError) -> str:
    winerror = getattr(exc, "winerror", None)
    if winerror is not None:
        return f"{type(exc).__name__}: winerror={winerror}"
    if exc.errno is not None:
        return f"{type(exc).__name__}: errno={exc.errno}"
    return type(exc).__name__


def _parse_roots(values: Iterable[str] | None) -> tuple[Path, ...] | None:
    if not values:
        return None
    return tuple(Path(os.path.abspath(os.path.expanduser(value))) for value in values)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Count files on local PC storage.")
    parser.add_argument(
        "roots",
        nargs="*",
        help="Optional root paths. When omitted, all local fixed drives are scanned.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Print machine-readable JSON instead of a short text summary.",
    )
    parser.add_argument(
        "--include-windows-setup",
        action="store_true",
        help="Include Windows setup/system folders that are excluded by default.",
    )
    parser.add_argument(
        "--count",
        choices=(COUNT_FILES, COUNT_DIRECTORIES),
        default=COUNT_FILES,
        help="Choose the single number printed in normal output.",
    )
    args = parser.parse_args(argv)

    result = count_files(
        _parse_roots(args.roots),
        exclude_windows_setup=not args.include_windows_setup,
        count_mode=args.count,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        number = result.total_files if args.count == COUNT_FILES else result.directories_counted
        print(number)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
