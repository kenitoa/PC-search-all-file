import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from counter import COUNT_DIRECTORIES, _is_windows_setup_directory, count_files


class CountFilesTests(unittest.TestCase):
    def test_counts_nested_files_without_following_directory_links(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            nested = root / "nested"
            nested.mkdir()
            (root / "a.txt").write_text("a", encoding="utf-8")
            (nested / "b.txt").write_text("b", encoding="utf-8")

            link = root / "linked_nested"
            try:
                os.symlink(nested, link, target_is_directory=True)
            except (OSError, NotImplementedError):
                link = None

            result = count_files([root])

            self.assertEqual(result.total_files, 2)
            self.assertEqual(result.directories_scanned, 2)
            self.assertEqual(result.directories_counted, 2)
            self.assertEqual(len(result.excluded_windows_setup_directories), 0)
            self.assertEqual(len(result.errors), 0)
            if link is not None:
                self.assertTrue(link.exists())

    def test_missing_root_is_reported_as_error(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            missing = Path(temp_dir) / "missing"

            result = count_files([missing])

            self.assertEqual(result.total_files, 0)
            self.assertEqual(result.directories_scanned, 0)
            self.assertEqual(len(result.errors), 1)
            self.assertIn("missing", result.errors[0].path)

    def test_directory_count_mode_returns_folder_number_without_file_count(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "nested").mkdir()
            (root / "a.txt").write_text("a", encoding="utf-8")

            result = count_files([root], count_mode=COUNT_DIRECTORIES)

            self.assertEqual(result.total_files, 0)
            self.assertEqual(result.directories_counted, 2)

    def test_cli_default_output_is_only_one_number(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "a.txt").write_text("a", encoding="utf-8")

            completed = subprocess.run(
                [sys.executable, str(Path(__file__).with_name("counter.py")), str(root)],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertEqual(completed.stdout.strip(), "1")

    @unittest.skipUnless(os.name == "nt", "Windows setup folder policy is Windows-only")
    def test_windows_setup_folders_are_excluded_at_drive_root(self):
        drive_root = Path("C:\\")

        self.assertTrue(_is_windows_setup_directory(drive_root, Path("C:\\Windows")))
        self.assertTrue(_is_windows_setup_directory(drive_root, Path("C:\\ProgramData")))
        self.assertFalse(
            _is_windows_setup_directory(
                Path("C:\\Users\\abc20\\workspace"),
                Path("C:\\Users\\abc20\\workspace\\Windows"),
            )
        )


if __name__ == "__main__":
    unittest.main()
