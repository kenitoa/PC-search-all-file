"""File search logic for local PC storage."""

from .searcher import (
    FileRecord,
    FileSearchIndex,
    EnsureIndexResult,
    ErrorVerification,
    RootFingerprint,
    ScanError,
    SearchResult,
    bootstrap_install_index,
    build_file_index,
    ensure_index,
    index_is_stale,
    load_index,
    resolve_index_roots,
    save_index,
    verify_scan_errors,
    watch_index,
)

__all__ = [
    "FileRecord",
    "FileSearchIndex",
    "EnsureIndexResult",
    "ErrorVerification",
    "RootFingerprint",
    "ScanError",
    "SearchResult",
    "bootstrap_install_index",
    "build_file_index",
    "ensure_index",
    "index_is_stale",
    "load_index",
    "resolve_index_roots",
    "save_index",
    "verify_scan_errors",
    "watch_index",
]
