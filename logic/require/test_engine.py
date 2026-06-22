import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from engine import execute_search_requirement, require_search


class RequireEngineTests(unittest.TestCase):
    def test_requirement_flow_counts_before_searching_same_roots(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "alpha-report.txt").write_text("alpha", encoding="utf-8")
            (root / "notes.md").write_text("notes", encoding="utf-8")

            result = execute_search_requirement("alpha", [root])

            self.assertEqual(result.steps, ("require", "counting", "search"))
            self.assertEqual(result.count.total_files, 2)
            self.assertEqual(result.search.total, 1)
            self.assertEqual(result.search.matches[0].name, "alpha-report.txt")

    def test_rejects_empty_query_before_counting_or_searching(self):
        with self.assertRaises(ValueError):
            require_search("   ", [Path.cwd()])

    def test_saved_index_roots_are_used_when_roots_are_omitted(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir)
            root = workspace / "data"
            root.mkdir()
            index_path = workspace / "index.json"
            (root / "first.txt").write_text("first", encoding="utf-8")

            first = execute_search_requirement("first", [root], index_path=index_path)

            self.assertTrue(first.index_rebuilt)
            self.assertEqual(first.count.total_files, 1)

            second = execute_search_requirement("first", index_path=index_path)

            self.assertEqual(second.requirement.roots, (str(root),))
            self.assertEqual(second.count.total_files, 1)
            self.assertEqual(second.search.total, 1)

    def test_cli_outputs_json_with_required_steps(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            (root / "budget.xlsx").write_text("budget", encoding="utf-8")

            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("engine.py")),
                    "budget",
                    str(root),
                    "--json",
                ],
                check=True,
                capture_output=True,
                text=True,
            )

            self.assertIn('"steps":', completed.stdout)
            self.assertIn('"counting"', completed.stdout)
            self.assertIn("budget.xlsx", completed.stdout)


if __name__ == "__main__":
    unittest.main()
