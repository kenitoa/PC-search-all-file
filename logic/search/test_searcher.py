import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from searcher import (
    SEARCH_CONTAINS,
    SEARCH_EXACT,
    SEARCH_EXTENSION,
    SEARCH_PATH,
    bootstrap_install_index,
    build_file_index,
    ensure_index,
    index_is_stale,
    load_index,
    resolve_index_roots,
    save_index,
    verify_scan_errors,
    watch_index,
    ScanError,
)


class FileSearchIndexTests(unittest.TestCase):
    def test_indexes_files_once_and_searches_by_hash_tables(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            docs = root / "docs"
            images = root / "images"
            docs.mkdir()
            images.mkdir()
            (docs / "Alpha Report.txt").write_text("a", encoding="utf-8")
            (images / "alpha-diagram.png").write_text("b", encoding="utf-8")
            (root / "notes.md").write_text("c", encoding="utf-8")

            index = build_file_index([root])

            self.assertEqual(index.total_files, 3)
            self.assertEqual(index.search("Alpha Report.txt", mode=SEARCH_EXACT).total, 1)
            self.assertEqual(index.search("alpha", mode=SEARCH_CONTAINS).total, 2)
            self.assertEqual(index.search(".md", mode=SEARCH_EXTENSION).matches[0].name, "notes.md")
            self.assertEqual(index.search("docs", mode=SEARCH_PATH).matches[0].name, "Alpha Report.txt")

    def test_workspace_root_expands_to_parent_for_first_index_pass(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            workspace = parent / "PC 전체 접근 권한"
            sibling = parent / "sibling"
            workspace.mkdir()
            sibling.mkdir()
            (workspace / "inside.txt").write_text("inside", encoding="utf-8")
            (sibling / "outside.txt").write_text("outside", encoding="utf-8")

            roots = resolve_index_roots([workspace])
            index = build_file_index([workspace])

            self.assertEqual(roots, (parent,))
            self.assertEqual(index.search("outside.txt", mode=SEARCH_EXACT).total, 1)

    def test_does_not_follow_directory_symlink_cycles(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested"
            nested.mkdir()
            (nested / "target.txt").write_text("target", encoding="utf-8")
            try:
                os.symlink(root, nested / "loop", target_is_directory=True)
            except (OSError, NotImplementedError):
                pass

            index = build_file_index([root])

            self.assertEqual(index.search("target.txt", mode=SEARCH_EXACT).total, 1)

    def test_missing_root_is_reported_as_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"

            index = build_file_index([missing])

            self.assertEqual(index.total_files, 0)
            self.assertEqual(len(index.errors), 1)
            self.assertEqual(index.error_verification.initial_error_count, 1)
            self.assertEqual(index.error_verification.recovered_error_count, 0)
            self.assertIn("missing", index.errors[0].path)

    def test_error_verification_can_recover_transient_error_records(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "now_exists.txt"
            path.write_text("ready", encoding="utf-8")

            verification = verify_scan_errors([ScanError(str(path), "previous failure")])

            self.assertEqual(verification.initial_error_count, 1)
            self.assertEqual(verification.recovered_error_count, 1)
            self.assertEqual(len(verification.unresolved_errors), 0)

    def test_extensionless_files_do_not_create_dot_extension_entries(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "LICENSE").write_text("license", encoding="utf-8")

            index = build_file_index([root])

            self.assertEqual(index.records[0].extension, "")
            self.assertEqual(index.search(".", mode=SEARCH_EXTENSION).total, 0)

    def test_index_can_be_saved_and_loaded_for_repeated_fast_searches(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index_path = root / "index.json"
            (root / "resume.pdf").write_text("r", encoding="utf-8")

            save_index(build_file_index([root]), index_path)
            loaded = load_index(index_path)

            self.assertEqual(loaded.search("resume", mode=SEARCH_CONTAINS).total, 1)

    def test_ensure_index_rebuilds_when_saved_fingerprint_is_stale(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index_path = root / "index.json"
            (root / "first.txt").write_text("first", encoding="utf-8")
            first = ensure_index([root], index_path=index_path)
            (root / "second.txt").write_text("second", encoding="utf-8")

            self.assertFalse(first.rebuilt is False)
            self.assertTrue(index_is_stale(load_index(index_path)))

            second = ensure_index([root], index_path=index_path)

            self.assertTrue(second.rebuilt)
            self.assertEqual(second.reason, "stale")
            self.assertEqual(second.index.search("second.txt", mode=SEARCH_EXACT).total, 1)

    def test_bootstrap_install_index_expands_workspace_to_parent(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            parent = Path(temp_dir)
            workspace = parent / "PC 전체 접근 권한"
            sibling = parent / "sibling"
            workspace.mkdir()
            sibling.mkdir()
            index_path = workspace / "index.json"
            (sibling / "installed-neighbor.txt").write_text("neighbor", encoding="utf-8")

            result = bootstrap_install_index(workspace, index_path=index_path)

            self.assertTrue(result.rebuilt)
            self.assertEqual(result.index.roots, (str(parent),))
            self.assertEqual(
                result.index.search("installed-neighbor.txt", mode=SEARCH_EXACT).total,
                1,
            )

    def test_watch_index_rebuilds_stale_index_without_manual_search(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index_path = root / "index.json"
            (root / "before.txt").write_text("before", encoding="utf-8")
            ensure_index([root], index_path=index_path)
            (root / "after.txt").write_text("after", encoding="utf-8")

            result = watch_index([root], index_path=index_path, interval_seconds=0.01, cycles=0)

            self.assertTrue(result.rebuilt)
            self.assertEqual(result.index.search("after.txt", mode=SEARCH_EXACT).total, 1)

    def test_cli_can_build_index_then_search_it(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index_path = root / "index.json"
            (root / "budget.xlsx").write_text("b", encoding="utf-8")

            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("searcher.py")),
                    "index",
                    str(root),
                    "--output",
                    str(index_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("searcher.py")),
                    "find",
                    "budget",
                    "--index",
                    str(index_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("budget.xlsx", completed.stdout)

    def test_cli_find_refreshes_stale_saved_index_automatically(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            index_path = root / "index.json"
            (root / "old.txt").write_text("old", encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("searcher.py")),
                    "index",
                    str(root),
                    "--output",
                    str(index_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            (root / "new.txt").write_text("new", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("searcher.py")),
                    "find",
                    "new.txt",
                    "--mode",
                    "exact",
                    "--index",
                    str(index_path),
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn("new.txt", completed.stdout)


if __name__ == "__main__":
    unittest.main()
