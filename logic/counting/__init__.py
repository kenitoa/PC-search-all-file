"""File counting logic for local PC storage."""

from .counter import CountResult, RootCount, count_files, discover_local_roots

__all__ = [
    "CountResult",
    "RootCount",
    "count_files",
    "discover_local_roots",
]
