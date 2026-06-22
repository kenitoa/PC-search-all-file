"""Requirement-first search engine orchestration.

The upper interface should call this layer, not the lower search module
directly. This keeps the runtime path auditable as require -> counting -> search.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Sequence


if __package__ in {None, ""}:
    sys.path.insert(0, os.fspath(Path(__file__).resolve().parents[2]))

from logic.counting.counter import CountResult, count_files
from logic.search.searcher import (
    SEARCH_CONTAINS,
    SEARCH_MODES,
    EnsureIndexResult,
    SearchResult,
    build_file_index,
    ensure_index,
    load_index,
    resolve_index_roots,
)


EXECUTION_STEPS = ("require", "counting", "search")


@dataclass(frozen=True)
class SearchRequirement:
    query: str
    roots: tuple[str, ...]
    mode: str = SEARCH_CONTAINS
    limit: int | None = 50
    index_path: str | None = None
    exclude_windows_setup: bool = True
    refresh_index: bool = True

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class RequirementExecution:
    requirement: SearchRequirement
    count: CountResult
    search: SearchResult
    index_path: str | None
    index_rebuilt: bool
    index_reason: str
    steps: tuple[str, str, str] = EXECUTION_STEPS

    def to_dict(self) -> dict[str, object]:
        return {
            "steps": self.steps,
            "requirement": self.requirement.to_dict(),
            "count": self.count.to_dict(),
            "search": self.search.to_dict(),
            "index": {
                "path": self.index_path,
                "rebuilt": self.index_rebuilt,
                "reason": self.index_reason,
            },
        }


def require_search(
    query: str,
    roots: Sequence[Path | str] | None = None,
    *,
    mode: str = SEARCH_CONTAINS,
    limit: int | None = 50,
    index_path: Path | str | None = None,
    exclude_windows_setup: bool = True,
    include_workspace_parent: bool = True,
    refresh_index: bool = True,
) -> SearchRequirement:
    """Validate and normalize a search request from the upper interface."""
    normalized_query = query.strip() if isinstance(query, str) else ""
    if not normalized_query:
        raise ValueError("query is required")
    if mode not in SEARCH_MODES:
        raise ValueError(f"unsupported search mode: {mode}")
    if limit is not None and limit <= 0:
        raise ValueError("limit must be greater than zero")

    resolved_roots = _resolve_requirement_roots(
        roots,
        index_path=index_path,
        include_workspace_parent=include_workspace_parent,
    )
    return SearchRequirement(
        normalized_query,
        tuple(os.fspath(root) for root in resolved_roots),
        mode,
        limit,
        os.fspath(Path(index_path)) if index_path is not None else None,
        exclude_windows_setup,
        refresh_index,
    )


def execute_search_requirement(
    query: str,
    roots: Sequence[Path | str] | None = None,
    *,
    mode: str = SEARCH_CONTAINS,
    limit: int | None = 50,
    index_path: Path | str | None = None,
    exclude_windows_setup: bool = True,
    include_workspace_parent: bool = True,
    refresh_index: bool = True,
) -> RequirementExecution:
    """Run require -> counting -> search for one interface request."""
    requirement = require_search(
        query,
        roots,
        mode=mode,
        limit=limit,
        index_path=index_path,
        exclude_windows_setup=exclude_windows_setup,
        include_workspace_parent=include_workspace_parent,
        refresh_index=refresh_index,
    )
    count = count_files(
        requirement.roots,
        exclude_windows_setup=requirement.exclude_windows_setup,
    )

    index_result = _load_or_build_index(requirement)
    search = index_result.index.search(
        requirement.query,
        mode=requirement.mode,
        limit=requirement.limit,
    )
    return RequirementExecution(
        requirement,
        count,
        search,
        index_result.index_path,
        index_result.rebuilt,
        index_result.reason,
    )


def _resolve_requirement_roots(
    roots: Sequence[Path | str] | None,
    *,
    index_path: Path | str | None,
    include_workspace_parent: bool,
) -> tuple[Path, ...]:
    if roots:
        return resolve_index_roots(roots, include_workspace_parent=include_workspace_parent)

    if index_path is not None and Path(index_path).exists():
        existing = load_index(index_path)
        return resolve_index_roots(existing.roots, include_workspace_parent=False)

    return resolve_index_roots(None, include_workspace_parent=include_workspace_parent)


def _load_or_build_index(requirement: SearchRequirement) -> EnsureIndexResult:
    roots = tuple(Path(root) for root in requirement.roots)
    if requirement.index_path is None:
        index = build_file_index(
            roots,
            exclude_windows_setup=requirement.exclude_windows_setup,
            include_workspace_parent=False,
        )
        return EnsureIndexResult(index, "", True, "live")

    if requirement.refresh_index:
        return ensure_index(
            roots,
            index_path=requirement.index_path,
            exclude_windows_setup=requirement.exclude_windows_setup,
            include_workspace_parent=False,
        )

    if Path(requirement.index_path).exists():
        return EnsureIndexResult(load_index(requirement.index_path), requirement.index_path, False, "loaded")

    index = build_file_index(
        roots,
        exclude_windows_setup=requirement.exclude_windows_setup,
        include_workspace_parent=False,
    )
    return EnsureIndexResult(index, requirement.index_path, True, "live")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Run the upper search-interface engine as require -> counting -> search."
    )
    parser.add_argument("query", help="File name, extension, token, or path fragment to find.")
    parser.add_argument("roots", nargs="*", help="Optional roots. Omitted means saved roots or drives.")
    parser.add_argument("--index", default=None, help="Saved index JSON path.")
    parser.add_argument(
        "--mode",
        choices=tuple(sorted(SEARCH_MODES)),
        default=SEARCH_CONTAINS,
        help="Search strategy.",
    )
    parser.add_argument("--limit", type=int, default=50, help="Maximum matches to print.")
    parser.add_argument("--json", action="store_true", help="Print requirement, count, and search JSON.")
    parser.add_argument(
        "--include-windows-setup",
        action="store_true",
        help="Include Windows setup/system folders that are excluded by default.",
    )
    parser.add_argument(
        "--no-auto-refresh",
        action="store_true",
        help="Do not rebuild a stale saved index before searching.",
    )
    args = parser.parse_args(argv)

    result = execute_search_requirement(
        args.query,
        args.roots,
        mode=args.mode,
        limit=args.limit,
        index_path=args.index,
        exclude_windows_setup=not args.include_windows_setup,
        refresh_index=not args.no_auto_refresh,
    )
    if args.json:
        print(json.dumps(result.to_dict(), ensure_ascii=False, indent=2))
    else:
        for match in result.search.matches:
            print(match.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
