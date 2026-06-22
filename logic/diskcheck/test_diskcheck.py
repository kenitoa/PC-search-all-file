import unittest

from diskcheck import (
    DRIVE_FIXED,
    DRIVE_REMOVABLE,
    check_disks,
)


class DiskCheckTests(unittest.TestCase):
    def test_reports_fixed_visible_drive_as_searchable(self):
        report = check_disks(
            ["C:\\"],
            filesystem_exists=lambda root: root == "C:\\",
            logical_drive_provider=lambda: {"C:\\": DRIVE_FIXED},
        )

        self.assertEqual(report.searchable_roots, ("C:\\",))
        self.assertEqual(report.missing_expected_roots, ())
        self.assertTrue(report.statuses[0].searchable_by_default)

    def test_distinguishes_missing_d_drive_from_search_failure(self):
        report = check_disks(
            ["C:\\", "D:\\"],
            filesystem_exists=lambda root: root == "C:\\",
            logical_drive_provider=lambda: {"C:\\": DRIVE_FIXED},
        )

        self.assertEqual(report.searchable_roots, ("C:\\",))
        self.assertEqual(report.missing_expected_roots, ("D:\\",))
        self.assertFalse(report.statuses[1].windows_logical)
        self.assertIn("not visible", report.statuses[1].reason)

    def test_blocks_non_fixed_logical_drive_by_default(self):
        report = check_disks(
            ["D:\\"],
            filesystem_exists=lambda root: root == "D:\\",
            logical_drive_provider=lambda: {"D:\\": DRIVE_REMOVABLE},
        )

        self.assertEqual(report.searchable_roots, ())
        self.assertEqual(report.blocked_expected_roots, ("D:\\",))
        self.assertEqual(report.statuses[0].drive_type_name, "removable")

    def test_normalizes_drive_letters(self):
        report = check_disks(
            ["c", "d:"],
            filesystem_exists=lambda root: root in {"C:\\", "D:\\"},
            logical_drive_provider=lambda: {"C:\\": DRIVE_FIXED, "D:\\": DRIVE_FIXED},
        )

        self.assertEqual(report.expected_roots, ("C:\\", "D:\\"))
        self.assertEqual(report.searchable_roots, ("C:\\", "D:\\"))


if __name__ == "__main__":
    unittest.main()
