"""Hash-table based file search for local PC storage.

This module owns indexing and lookup. A first pass walks accessible files and
builds hash tables; later searches read only those tables.
"""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Mapping, Sequence


DRIVE_FIXED = 3
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

SEARCH_EXACT = "exact"
SEARCH_CONTAINS = "contains"
SEARCH_PREFIX = "prefix"
SEARCH_EXTENSION = "extension"
SEARCH_PATH = "path"
SEARCH_TOKEN = "token"
SEARCH_MODES = frozenset(
    {SEARCH_EXACT, SEARCH_CONTAINS, SEARCH_PREFIX, SEARCH_EXTENSION, SEARCH_PATH, SEARCH_TOKEN}
)
PC_ACCESS_FOLDER_KEYS = frozenset({"pc전체접근권한", "pc전체접근"})
NGRAM_SIZE = 3


@dataclass(frozen=True)
class ScanError:
    path: str
    error: str


@dataclass(frozen=True)
class FileRecord:
    id: int
    path: str
    name: str
    directory: str
    extension: str
    size: int | None = None
    modified_ns: int | None = None


@dataclass(frozen=True)
class RootFingerprint:
    root: str
    files: int
    directories: int
    newest_modified_ns: int
    digest: str


@dataclass(frozen=True)
class ErrorVerification:
    initial_error_count: int
    recovered_error_count: int
    unresolved_errors: tuple[ScanError, ...]


@dataclass(frozen=True)
class EnsureIndexResult:
    index: "FileSearchIndex"
    index_path: str
    rebuilt: bool
    reason: str


@dataclass(frozen=True)
class SearchResult:
    query: str
    mode: str
    matches: tuple[FileRecord, ...]
    candidate_count: int
    error_count: int

    @property
    def total(self) -> int:
        return len(self.matches)

    def to_dict(self) -> dict[str, object]:
        data = asdict(self)
        data["total"] = self.total
        return data


@dataclass(frozen=True)
class FileSearchIndex:
    roots: tuple[str, ...]
    records: tuple[FileRecord, ...]
    exact_name: Mapping[str, tuple[int, ...]]
    extension: Mapping[str, tuple[int, ...]]
    token: Mapping[str, tuple[int, ...]]
    name_ngram: Mapping[str, tuple[int, ...]]
    path_ngram: Mapping[str, tuple[int, ...]]
    errors: tuple[ScanError, ...] = ()
    fingerprints: tuple[RootFingerprint, ...] = ()
    error_verification: ErrorVerification | None = None
    exclude_windows_setup: bool = True

    @property
    def total_files(self) -> int:
        return len(self.records)

    def search(
        self,
        query: str,
        *,
        mode: str = SEARCH_CONTAINS,
        limit: int | None = None,
    ) -> SearchResult:
        """Search indexed files without walking the filesystem again."""
        if mode not in SEARCH_MODES:
            raise ValueError(f"unsupported search mode: {mode}")

        normalized_query = _normalize_query(query)
        if not normalized_query:
            return SearchResult(query, mode, (), 0, len(self.errors))

        candidate_ids = self._candidate_ids(normalized_query, mode)
        matches: list[FileRecord] = []
        for record_id in sorted(candidate_ids):
            record = self.records[record_id]
            if _record_matches(record, normalized_query, mode):
                matches.append(record)
                if limit is not None and len(matches) >= limit:
                    break

        return SearchResult(query, mode, tuple(matches), len(candidate_ids), len(self.errors))

    def _candidate_ids(self, normalized_query: str, mode: str) -> set[int]:
        if mode == SEARCH_EXACT:
            return set(self.exact_name.get(normalized_query, ()))
        if mode == SEARCH_EXTENSION:
            return set(self.extension.get(_normalize_extension(normalized_query), ()))
        if mode == SEARCH_TOKEN:
            return set(self.token.get(normalized_query, ()))
        if mode == SEARCH_PREFIX:
            query_ngrams = _ngrams(normalized_query)
            if query_ngrams:
                return _intersect_lookup(self.name_ngram, query_ngrams)
            return set(range(len(self.records)))
        if mode == SEARCH_PATH:
            query_ngrams = _ngrams(normalized_query)
            if query_ngrams:
                return _intersect_lookup(self.path_ngram, query_ngrams)
            return set(range(len(self.records)))

        query_ngrams = _ngrams(normalized_query)
        if not query_ngrams:
            # Short queries cannot use trigrams. Token/exact tables still avoid
            # a full filesystem walk, and the in-memory filter handles the rest.
            direct = set(self.token.get(normalized_query, ()))
            direct.update(self.exact_name.get(normalized_query, ()))
            return direct or set(range(len(self.records)))
        return _intersect_lookup(self.name_ngram, query_ngrams)

    def to_dict(self) -> dict[str, object]:
        return {
            "version": 1,
            "roots": list(self.roots),
            "records": [asdict(record) for record in self.records],
            "exact_name": _jsonify_index(self.exact_name),
            "extension": _jsonify_index(self.extension),
            "token": _jsonify_index(self.token),
            "name_ngram": _jsonify_index(self.name_ngram),
            "path_ngram": _jsonify_index(self.path_ngram),
            "errors": [asdict(error) for error in self.errors],
            "fingerprints": [asdict(fingerprint) for fingerprint in self.fingerprints],
            "error_verification": (
                {
                    "initial_error_count": self.error_verification.initial_error_count,
                    "recovered_error_count": self.error_verification.recovered_error_count,
                    "unresolved_errors": [
                        asdict(error) for error in self.error_verification.unresolved_errors
                    ],
                }
                if self.error_verification
                else None
            ),
            "exclude_windows_setup": self.exclude_windows_setup,
            "total_files": self.total_files,
            "error_count": len(self.errors),
        }


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


def resolve_index_roots(
    roots: Sequence[Path | str] | None = None,
    *,
    include_workspace_parent: bool = True,
) -> tuple[Path, ...]:
    """Resolve roots for indexing.

    When the selected folder is the "PC full access" workspace, the parent
    folder is indexed too, so searches cover files beside the workspace instead
    of only files inside the implementation folder.
    """
    selected = tuple(Path(root) for root in roots) if roots else discover_local_roots()
    resolved: list[Path] = []
    seen: set[str] = set()

    for root in selected:
        candidate = root.expanduser()
        if include_workspace_parent and _is_pc_access_workspace(candidate) and candidate.parent != candidate:
            candidate = candidate.parent

        absolute = Path(os.path.abspath(os.fspath(candidate)))
        key = os.path.normcase(os.fspath(absolute))
        if key not in seen:
            seen.add(key)
            resolved.append(absolute)

    return tuple(resolved)


def build_file_index(
    roots: Sequence[Path | str] | None = None,
    *,
    exclude_windows_setup: bool = True,
    include_workspace_parent: bool = True,
    verify_error_paths: bool = True,
) -> FileSearchIndex:
    """Walk roots once and build hash tables for fast repeated searches."""
    selected_roots = resolve_index_roots(
        roots,
        include_workspace_parent=include_workspace_parent,
    )
    records: list[FileRecord] = []
    errors: list[ScanError] = []
    exact_name: dict[str, set[int]] = {}
    extension: dict[str, set[int]] = {}
    token: dict[str, set[int]] = {}
    name_ngram: dict[str, set[int]] = {}
    path_ngram: dict[str, set[int]] = {}
    seen_paths: set[str] = set()
    fingerprints: list[RootFingerprint] = []

    for root in selected_roots:
        fingerprints.append(
            _index_root(
                root,
                records,
                errors,
                seen_paths,
                exact_name,
                extension,
                token,
                name_ngram,
                path_ngram,
                exclude_windows_setup,
            )
        )

    unresolved_errors = tuple(errors)
    error_verification = ErrorVerification(len(errors), 0, unresolved_errors)
    if verify_error_paths:
        error_verification = verify_scan_errors(errors)
        unresolved_errors = error_verification.unresolved_errors

    return FileSearchIndex(
        tuple(os.fspath(root) for root in selected_roots),
        tuple(records),
        _freeze_index(exact_name),
        _freeze_index(extension),
        _freeze_index(token),
        _freeze_index(name_ngram),
        _freeze_index(path_ngram),
        unresolved_errors,
        tuple(fingerprints),
        error_verification,
        exclude_windows_setup,
    )


def save_index(index: FileSearchIndex, path: Path | str) -> None:
    target = Path(path)
    target.write_text(json.dumps(index.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")


def load_index(path: Path | str) -> FileSearchIndex:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if payload.get("version") != 1:
        raise ValueError("unsupported search index version")

    error_verification_payload = payload.get("error_verification")
    error_verification = None
    if error_verification_payload:
        error_verification = ErrorVerification(
            error_verification_payload["initial_error_count"],
            error_verification_payload["recovered_error_count"],
            tuple(
                ScanError(**error)
                for error in error_verification_payload.get("unresolved_errors", ())
            ),
        )

    return FileSearchIndex(
        tuple(payload["roots"]),
        tuple(FileRecord(**record) for record in payload["records"]),
        _load_index_table(payload["exact_name"]),
        _load_index_table(payload["extension"]),
        _load_index_table(payload["token"]),
        _load_index_table(payload["name_ngram"]),
        _load_index_table(payload["path_ngram"]),
        tuple(ScanError(**error) for error in payload.get("errors", ())),
        tuple(RootFingerprint(**fingerprint) for fingerprint in payload.get("fingerprints", ())),
        error_verification,
        payload.get("exclude_windows_setup", True),
    )


def verify_scan_errors(errors: Sequence[ScanError]) -> ErrorVerification:
    """Re-check scan errors once and return only errors that still fail."""
    unresolved: list[ScanError] = []
    for error in errors:
        path = Path(error.path)
        try:
            if path.is_dir():
                with os.scandir(path):
                    pass
            else:
                path.stat()
        except OSError as exc:
            unresolved.append(ScanError(error.path, _format_error(exc)))

    return ErrorVerification(
        len(errors),
        len(errors) - len(unresolved),
        tuple(unresolved),
    )


def compute_root_fingerprints(
    roots: Sequence[Path | str],
    *,
    exclude_windows_setup: bool = True,
) -> tuple[RootFingerprint, ...]:
    """Compute current root fingerprints without building search tables."""
    return tuple(
        _fingerprint_root(Path(root), exclude_windows_setup)
        for root in resolve_index_roots(roots, include_workspace_parent=False)
    )


def index_is_stale(
    index: FileSearchIndex,
    *,
    exclude_windows_setup: bool = True,
) -> bool:
    """Return True when files under indexed roots differ from the saved snapshot."""
    if not index.fingerprints:
        return True
    current = compute_root_fingerprints(index.roots, exclude_windows_setup=exclude_windows_setup)
    return current != index.fingerprints


def ensure_index(
    roots: Sequence[Path | str] | None = None,
    *,
    index_path: Path | str | None = None,
    exclude_windows_setup: bool = True,
    include_workspace_parent: bool = True,
    force: bool = False,
) -> EnsureIndexResult:
    """Create an index immediately, or rebuild it when the saved snapshot is stale."""
    target = Path(index_path) if index_path else _default_index_path()
    reason = "missing"
    existing: FileSearchIndex | None = None

    if target.exists() and not force:
        existing = load_index(target)
        exclude_windows_setup = existing.exclude_windows_setup
        if not index_is_stale(existing, exclude_windows_setup=exclude_windows_setup):
            return EnsureIndexResult(existing, os.fspath(target), False, "current")
        reason = "stale"
    elif force:
        reason = "forced"

    index = build_file_index(
        roots if roots is not None else (existing.roots if existing else None),
        exclude_windows_setup=exclude_windows_setup,
        include_workspace_parent=include_workspace_parent,
    )
    save_index(index, target)
    return EnsureIndexResult(index, os.fspath(target), True, reason)


def bootstrap_install_index(
    install_root: Path | str | None = None,
    *,
    index_path: Path | str | None = None,
    exclude_windows_setup: bool = True,
) -> EnsureIndexResult:
    """Build the first parent-aware index as soon as this folder is installed."""
    root = Path(install_root) if install_root else Path.cwd()
    return ensure_index(
        [root],
        index_path=index_path,
        exclude_windows_setup=exclude_windows_setup,
        include_workspace_parent=True,
    )


def watch_index(
    roots: Sequence[Path | str] | None = None,
    *,
    index_path: Path | str | None = None,
    interval_seconds: float = 5.0,
    cycles: int | None = None,
    exclude_windows_setup: bool = True,
) -> EnsureIndexResult:
    """Poll for file changes and rebuild the index automatically when needed."""
    if interval_seconds <= 0:
        raise ValueError("interval_seconds must be greater than zero")

    result = ensure_index(
        roots,
        index_path=index_path,
        exclude_windows_setup=exclude_windows_setup,
    )
    completed = 0
    while cycles is None or completed < cycles:
        time.sleep(interval_seconds)
        current = load_index(result.index_path)
        if index_is_stale(current, exclude_windows_setup=exclude_windows_setup):
            result = ensure_index(
                current.roots,
                index_path=result.index_path,
                exclude_windows_setup=exclude_windows_setup,
                force=True,
            )
        else:
            result = EnsureIndexResult(current, result.index_path, False, "current")
        completed += 1
    return result


def _index_root(
    root: Path,
    records: list[FileRecord],
    errors: list[ScanError],
    seen_paths: set[str],
    exact_name: dict[str, set[int]],
    extension: dict[str, set[int]],
    token: dict[str, set[int]],
    name_ngram: dict[str, set[int]],
    path_ngram: dict[str, set[int]],
    exclude_windows_setup: bool,
) -> RootFingerprint:
    stack = [root]
    files = 0
    directories = 0
    newest_modified_ns = 0
    hasher = hashlib.sha1()

    while stack:
        current = stack.pop()
        if exclude_windows_setup and _is_windows_setup_directory(root, current):
            continue

        try:
            with os.scandir(current) as entries:
                directories += 1
                _update_digest(hasher, "D", current, None, None)
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            record = _add_file_record(
                                Path(entry.path),
                                records,
                                errors,
                                seen_paths,
                                exact_name,
                                extension,
                                token,
                                name_ngram,
                                path_ngram,
                            )
                            if record is not None:
                                files += 1
                                newest_modified_ns = max(
                                    newest_modified_ns,
                                    record.modified_ns or 0,
                                )
                                _update_digest(
                                    hasher,
                                    "F",
                                    Path(record.path),
                                    record.size,
                                    record.modified_ns,
                                )
                    except OSError as exc:
                        errors.append(ScanError(entry.path, _format_error(exc)))
        except OSError as exc:
            errors.append(ScanError(str(current), _format_error(exc)))

    return RootFingerprint(
        os.fspath(Path(os.path.abspath(os.fspath(root)))),
        files,
        directories,
        newest_modified_ns,
        hasher.hexdigest(),
    )


def _add_file_record(
    path: Path,
    records: list[FileRecord],
    errors: list[ScanError],
    seen_paths: set[str],
    exact_name: dict[str, set[int]],
    extension: dict[str, set[int]],
    token: dict[str, set[int]],
    name_ngram: dict[str, set[int]],
    path_ngram: dict[str, set[int]],
) -> FileRecord | None:
    absolute = Path(os.path.abspath(os.fspath(path)))
    path_key = os.path.normcase(os.fspath(absolute))
    if path_key in seen_paths:
        return None
    seen_paths.add(path_key)

    stat_result = None
    try:
        stat_result = path.stat()
    except OSError as exc:
        errors.append(ScanError(str(path), _format_error(exc)))

    record = FileRecord(
        id=len(records),
        path=os.fspath(absolute),
        name=absolute.name,
        directory=os.fspath(absolute.parent),
        extension=_normalize_extension(absolute.suffix),
        size=stat_result.st_size if stat_result else None,
        modified_ns=stat_result.st_mtime_ns if stat_result else None,
    )
    records.append(record)
    record_id = record.id
    normalized_name = _normalize_query(record.name)
    normalized_path = _normalize_query(record.path)

    _add_to_table(exact_name, normalized_name, record_id)
    if record.extension:
        _add_to_table(extension, record.extension, record_id)
    for part in _tokens(record.name):
        _add_to_table(token, part, record_id)
    for part in _tokens(record.path):
        _add_to_table(token, part, record_id)
    for ngram in _ngrams(normalized_name):
        _add_to_table(name_ngram, ngram, record_id)
    for ngram in _ngrams(normalized_path):
        _add_to_table(path_ngram, ngram, record_id)

    return record


def _record_matches(record: FileRecord, normalized_query: str, mode: str) -> bool:
    normalized_name = _normalize_query(record.name)
    normalized_path = _normalize_query(record.path)
    if mode == SEARCH_EXACT:
        return normalized_name == normalized_query
    if mode == SEARCH_EXTENSION:
        return record.extension == _normalize_extension(normalized_query)
    if mode == SEARCH_TOKEN:
        return normalized_query in _tokens(record.path)
    if mode == SEARCH_PREFIX:
        return normalized_name.startswith(normalized_query)
    if mode == SEARCH_PATH:
        return normalized_query in normalized_path
    return normalized_query in normalized_name


def _fingerprint_root(root: Path, exclude_windows_setup: bool) -> RootFingerprint:
    absolute_root = Path(os.path.abspath(os.fspath(root)))
    stack = [absolute_root]
    files = 0
    directories = 0
    newest_modified_ns = 0
    hasher = hashlib.sha1()

    while stack:
        current = stack.pop()
        if exclude_windows_setup and _is_windows_setup_directory(absolute_root, current):
            continue

        try:
            with os.scandir(current) as entries:
                directories += 1
                _update_digest(hasher, "D", current, None, None)
                for entry in entries:
                    try:
                        if entry.is_dir(follow_symlinks=False):
                            stack.append(Path(entry.path))
                        elif entry.is_file(follow_symlinks=False):
                            path = Path(entry.path)
                            stat_result = path.stat()
                            files += 1
                            newest_modified_ns = max(newest_modified_ns, stat_result.st_mtime_ns)
                            _update_digest(
                                hasher,
                                "F",
                                path,
                                stat_result.st_size,
                                stat_result.st_mtime_ns,
                            )
                    except OSError:
                        _update_digest(hasher, "E", Path(entry.path), None, None)
        except OSError:
            _update_digest(hasher, "E", current, None, None)

    return RootFingerprint(
        os.fspath(absolute_root),
        files,
        directories,
        newest_modified_ns,
        hasher.hexdigest(),
    )


def _update_digest(
    hasher: "hashlib._Hash",
    kind: str,
    path: Path,
    size: int | None,
    modified_ns: int | None,
) -> None:
    payload = "|".join(
        (
            kind,
            os.path.normcase(os.fspath(Path(os.path.abspath(os.fspath(path))))),
            "" if size is None else str(size),
            "" if modified_ns is None else str(modified_ns),
        )
    )
    hasher.update(payload.encode("utf-8", errors="surrogatepass"))
    hasher.update(b"\0")


def _is_pc_access_workspace(path: Path) -> bool:
    key = _normalize_query(path.name).replace(" ", "")
    return key in PC_ACCESS_FOLDER_KEYS


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

    if resolved_root.anchor and resolved_root == Path(resolved_root.anchor):
        return relative_parts[0] in WINDOWS_SETUP_ROOT_NAMES

    return False


def _normalize_query(value: str) -> str:
    return os.path.normcase(value).casefold().strip()


def _normalize_extension(value: str) -> str:
    normalized = _normalize_query(value)
    if not normalized:
        return ""
    return normalized if normalized.startswith(".") else f".{normalized}"


def _tokens(value: str) -> tuple[str, ...]:
    return tuple(
        part
        for part in re.split(r"[^0-9A-Za-z가-힣_]+", _normalize_query(value))
        if part
    )


def _ngrams(value: str, size: int = NGRAM_SIZE) -> tuple[str, ...]:
    compact = value.strip()
    if len(compact) < size:
        return ()
    return tuple({compact[index : index + size] for index in range(len(compact) - size + 1)})


def _add_to_table(table: dict[str, set[int]], key: str, record_id: int) -> None:
    if key:
        table.setdefault(key, set()).add(record_id)


def _intersect_lookup(table: Mapping[str, tuple[int, ...]], keys: Iterable[str]) -> set[int]:
    iterator = iter(keys)
    try:
        first = next(iterator)
    except StopIteration:
        return set()

    result = set(table.get(first, ()))
    for key in iterator:
        result.intersection_update(table.get(key, ()))
        if not result:
            break
    return result


def _freeze_index(table: Mapping[str, set[int]]) -> dict[str, tuple[int, ...]]:
    return {key: tuple(sorted(values)) for key, values in table.items()}


def _jsonify_index(table: Mapping[str, tuple[int, ...]]) -> dict[str, list[int]]:
    return {key: list(values) for key, values in table.items()}


def _load_index_table(payload: Mapping[str, Sequence[int]]) -> dict[str, tuple[int, ...]]:
    return {key: tuple(values) for key, values in payload.items()}


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


def _default_index_path() -> Path:
    digest = hashlib.sha1(os.fspath(Path.cwd()).encode("utf-8")).hexdigest()[:12]
    return Path.cwd() / f".pc-search-index-{digest}.json"


def _ensure_result_to_dict(result: EnsureIndexResult) -> dict[str, object]:
    verification = result.index.error_verification
    return {
        "index": result.index_path,
        "rebuilt": result.rebuilt,
        "reason": result.reason,
        "roots": result.index.roots,
        "total_files": result.index.total_files,
        "error_count": len(result.index.errors),
        "exclude_windows_setup": result.index.exclude_windows_setup,
        "initial_error_count": verification.initial_error_count if verification else len(result.index.errors),
        "recovered_error_count": verification.recovered_error_count if verification else 0,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Index and search local PC files.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    index_parser = subparsers.add_parser("index", help="Build a file search index.")
    index_parser.add_argument("roots", nargs="*", help="Optional roots. Omitted means local fixed drives.")
    index_parser.add_argument("--output", default=None, help="Index JSON output path.")
    index_parser.add_argument(
        "--include-windows-setup",
        action="store_true",
        help="Include Windows setup/system folders that are excluded by default.",
    )
    index_parser.add_argument(
        "--no-workspace-parent",
        action="store_true",
        help="Do not expand the PC full-access workspace root to its parent folder.",
    )
    index_parser.add_argument("--json", action="store_true", help="Print index metadata as JSON.")

    install_parser = subparsers.add_parser(
        "install",
        help="Create the parent-aware index immediately after installation.",
    )
    install_parser.add_argument(
        "root",
        nargs="?",
        default=".",
        help="Installed PC full-access folder. Defaults to the current folder.",
    )
    install_parser.add_argument("--index", default=None, help="Index JSON path.")
    install_parser.add_argument("--json", action="store_true", help="Print install metadata as JSON.")

    find_parser = subparsers.add_parser("find", help="Search a saved index, or build one live.")
    find_parser.add_argument("query", help="File name, extension, token, or path fragment to find.")
    find_parser.add_argument("roots", nargs="*", help="Optional live-build roots when --index is omitted.")
    find_parser.add_argument("--index", default=None, help="Existing index JSON path.")
    find_parser.add_argument(
        "--mode",
        choices=tuple(sorted(SEARCH_MODES)),
        default=SEARCH_CONTAINS,
        help="Search strategy.",
    )
    find_parser.add_argument("--limit", type=int, default=50, help="Maximum matches to print.")
    find_parser.add_argument("--json", action="store_true", help="Print detailed JSON.")
    find_parser.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="Do not rebuild a stale saved index before searching.",
    )

    watch_parser = subparsers.add_parser("watch", help="Automatically rebuild a stale index.")
    watch_parser.add_argument("roots", nargs="*", help="Optional roots. Omitted means saved roots or drives.")
    watch_parser.add_argument("--index", default=None, help="Index JSON path.")
    watch_parser.add_argument(
        "--interval",
        type=float,
        default=5.0,
        help="Polling interval in seconds.",
    )
    watch_parser.add_argument(
        "--cycles",
        type=int,
        default=None,
        help="Optional cycle limit for verification or scheduled runs.",
    )
    watch_parser.add_argument("--json", action="store_true", help="Print final watch metadata as JSON.")

    verify_parser = subparsers.add_parser(
        "verify-errors",
        help="Re-check unresolved errors stored in an index.",
    )
    verify_parser.add_argument("--index", required=True, help="Existing index JSON path.")
    verify_parser.add_argument(
        "--update",
        action="store_true",
        help="Save the index with the rechecked unresolved error list.",
    )
    verify_parser.add_argument("--json", action="store_true", help="Print verification JSON.")

    args = parser.parse_args(argv)
    if args.command == "index":
        index = build_file_index(
            _parse_roots(args.roots),
            exclude_windows_setup=not args.include_windows_setup,
            include_workspace_parent=not args.no_workspace_parent,
        )
        output = Path(args.output) if args.output else _default_index_path()
        save_index(index, output)
        if args.json:
            print(
                json.dumps(
                    {
                        "index": os.fspath(output),
                        "roots": index.roots,
                        "total_files": index.total_files,
                        "error_count": len(index.errors),
                        "initial_error_count": (
                            index.error_verification.initial_error_count
                            if index.error_verification
                            else len(index.errors)
                        ),
                        "recovered_error_count": (
                            index.error_verification.recovered_error_count
                            if index.error_verification
                            else 0
                        ),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(os.fspath(output))
        return 0

    if args.command == "install":
        result = bootstrap_install_index(args.root, index_path=args.index)
        if args.json:
            print(json.dumps(_ensure_result_to_dict(result), ensure_ascii=False, indent=2))
        else:
            print(result.index_path)
        return 0

    if args.command == "watch":
        result = watch_index(
            _parse_roots(args.roots),
            index_path=args.index,
            interval_seconds=args.interval,
            cycles=args.cycles,
        )
        if args.json:
            print(json.dumps(_ensure_result_to_dict(result), ensure_ascii=False, indent=2))
        else:
            print(result.index_path)
        return 0

    if args.command == "verify-errors":
        index = load_index(args.index)
        verification = verify_scan_errors(index.errors)
        checked = FileSearchIndex(
            index.roots,
            index.records,
            index.exact_name,
            index.extension,
            index.token,
            index.name_ngram,
            index.path_ngram,
            verification.unresolved_errors,
            index.fingerprints,
            verification,
            index.exclude_windows_setup,
        )
        if args.update:
            save_index(checked, args.index)
        if args.json:
            print(
                json.dumps(
                    {
                        "index": args.index,
                        "initial_error_count": verification.initial_error_count,
                        "recovered_error_count": verification.recovered_error_count,
                        "unresolved_error_count": len(verification.unresolved_errors),
                        "updated": args.update,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(len(verification.unresolved_errors))
        return 0

    if args.index and not args.no_auto_refresh:
        index = ensure_index(index_path=args.index).index
    else:
        index = load_index(args.index) if args.index else build_file_index(_parse_roots(args.roots))
    result = index.search(args.query, mode=args.mode, limit=args.limit)
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        for match in result.matches:
            print(match.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
