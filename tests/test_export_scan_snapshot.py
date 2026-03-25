"""
tests/test_export_scan_snapshot.py

Unit tests for services/worker/export_scan_snapshot.py.
Uses an isolated SQLite test DB — never touches tmp/app.db.
"""

import gc
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(REPO_ROOT))

from execution.db.sqlite import connect, init_db
from services.worker.export_scan_snapshot import export_scan_snapshot
from services.worker.run_all_scans import run_all_scans

TEST_DB_PATH = str(REPO_ROOT / "tmp" / "test_export_scan_snapshot.db")


class TestExportScanSnapshot(unittest.TestCase):

    def setUp(self) -> None:
        (REPO_ROOT / "tmp").mkdir(parents=True, exist_ok=True)
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)
        conn = connect(TEST_DB_PATH)
        init_db(conn)
        conn.commit()
        conn.close()

    def tearDown(self) -> None:
        gc.collect()
        if os.path.exists(TEST_DB_PATH):
            os.remove(TEST_DB_PATH)

    # ------------------------------------------------------------------
    # T1 — empty DB: snapshot shape
    # ------------------------------------------------------------------
    def test_t1_empty_db_shape(self):
        """T1: empty DB → correct type, scan_count, and presence of key fields."""
        snapshot = export_scan_snapshot(db_path=TEST_DB_PATH)
        self.assertEqual(snapshot["type"], "SCAN_SNAPSHOT")
        self.assertEqual(snapshot["scan_count"], 4)
        self.assertIn("generated_at", snapshot)
        self.assertIn("action_summary", snapshot)
        self.assertEqual(len(snapshot["scans"]), 4)

    # ------------------------------------------------------------------
    # T2 — scans matches run_all_scans results
    # ------------------------------------------------------------------
    def test_t2_scans_consistent_with_run_all_scans(self):
        """T2: scans list contains the same scan_names as run_all_scans results."""
        snapshot = export_scan_snapshot(db_path=TEST_DB_PATH)
        reference = run_all_scans(db_path=TEST_DB_PATH)
        snapshot_names = [s["scan_name"] for s in snapshot["scans"]]
        reference_names = [s["scan_name"] for s in reference["results"]]
        self.assertEqual(snapshot_names, reference_names)


if __name__ == "__main__":
    unittest.main()
