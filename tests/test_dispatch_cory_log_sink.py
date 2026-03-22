"""
tests/test_dispatch_cory_log_sink.py

Unit tests for execution/events/dispatch_cory_log_sink.py.

Fast, deterministic, no network, no DB.  Uses temp directories for
full isolation — nothing is written to tmp/ during these tests.

Scenarios covered:
    T1  — writes exactly one file to log_dir
    T2  — file JSON content matches all expected fields
    T3  — creates log_dir when it does not exist
    T4  — invalid row_data (missing required field) raises ValueError
    T5  — two calls with different now values create two distinct files
    T6  — returned dict contains dispatched=True, mode="log_sink", real path
"""

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from execution.events.dispatch_cory_log_sink import dispatch_cory_log_sink  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures — deterministic, shared across tests
# ---------------------------------------------------------------------------
_ROW = {
    "id":          7,
    "lead_id":     "DISPATCH_TEST_LEAD",
    "destination": "CORY_BOOKING",
    "reason":      "HOT_LEAD_BOOKING",
    "created_at":  "2026-03-22T22:47:00.000000Z",
}
_NOW  = "2026-03-22T23:00:00+00:00"
_NOW2 = "2026-03-22T23:05:00+00:00"


class TestDispatchCoryLogSink(unittest.TestCase):

    def setUp(self):
        # Each test gets its own isolated temp directory.
        self._tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        # Remove all files written during the test.
        import shutil
        shutil.rmtree(self._tmpdir, ignore_errors=True)

    def _call(self, row=None, now=_NOW, log_dir=None) -> dict:
        return dispatch_cory_log_sink(
            row or _ROW,
            now=now,
            log_dir=log_dir or self._tmpdir,
        )

    # ------------------------------------------------------------------
    # T1 — writes exactly one file to log_dir
    # ------------------------------------------------------------------
    def test_writes_exactly_one_file(self):
        self._call()

        files = list(Path(self._tmpdir).iterdir())
        self.assertEqual(len(files), 1)
        self.assertTrue(files[0].is_file())
        self.assertTrue(files[0].name.endswith(".json"))

    # ------------------------------------------------------------------
    # T2 — file JSON content matches all expected fields
    # ------------------------------------------------------------------
    def test_file_content_matches_expected_fields(self):
        self._call()

        file_path = next(Path(self._tmpdir).iterdir())
        content = json.loads(file_path.read_text(encoding="utf-8"))

        self.assertEqual(content["sync_record_id"], _ROW["id"])
        self.assertEqual(content["lead_id"],        _ROW["lead_id"])
        self.assertEqual(content["destination"],    _ROW["destination"])
        self.assertEqual(content["reason"],         _ROW["reason"])
        self.assertEqual(content["queued_at"],      _ROW["created_at"])
        self.assertEqual(content["dispatched_at"],  _NOW)
        self.assertEqual(content["mode"],           "log_sink")
        self.assertTrue(content["dispatched"])

    # ------------------------------------------------------------------
    # T3 — creates log_dir when it does not exist
    # ------------------------------------------------------------------
    def test_creates_log_dir_if_missing(self):
        new_dir = os.path.join(self._tmpdir, "nested", "log")
        self.assertFalse(os.path.exists(new_dir))

        self._call(log_dir=new_dir)

        self.assertTrue(os.path.isdir(new_dir))
        self.assertEqual(len(list(Path(new_dir).iterdir())), 1)

    # ------------------------------------------------------------------
    # T4 — missing required field raises ValueError
    # ------------------------------------------------------------------
    def test_missing_required_field_raises_value_error(self):
        bad_row = {k: v for k, v in _ROW.items() if k != "lead_id"}

        with self.assertRaises(ValueError) as ctx:
            dispatch_cory_log_sink(bad_row, now=_NOW, log_dir=self._tmpdir)

        self.assertIn("lead_id", str(ctx.exception))

    def test_missing_id_raises_value_error(self):
        bad_row = {k: v for k, v in _ROW.items() if k != "id"}

        with self.assertRaises(ValueError) as ctx:
            dispatch_cory_log_sink(bad_row, now=_NOW, log_dir=self._tmpdir)

        self.assertIn("id", str(ctx.exception).lower())

    # ------------------------------------------------------------------
    # T5 — two calls with different now values create two distinct files
    # ------------------------------------------------------------------
    def test_two_calls_produce_two_distinct_files(self):
        self._call(now=_NOW)
        self._call(now=_NOW2)

        files = sorted(Path(self._tmpdir).iterdir())
        self.assertEqual(len(files), 2)
        self.assertNotEqual(files[0].name, files[1].name)

    # ------------------------------------------------------------------
    # T6 — returned dict shape: dispatched=True, mode=log_sink, real path
    # ------------------------------------------------------------------
    def test_returned_dict_shape(self):
        result = self._call()

        self.assertTrue(result["dispatched"])
        self.assertEqual(result["mode"], "log_sink")
        self.assertIn("path", result)
        self.assertTrue(os.path.isfile(result["path"]))

    # ------------------------------------------------------------------
    # T7 — sync_record_id alias accepted
    # ------------------------------------------------------------------
    def test_sync_record_id_alias_accepted(self):
        aliased_row = {k: v for k, v in _ROW.items() if k != "id"}
        aliased_row["sync_record_id"] = _ROW["id"]

        result = self._call(row=aliased_row)

        self.assertTrue(result["dispatched"])
        content = json.loads(Path(result["path"]).read_text(encoding="utf-8"))
        self.assertEqual(content["sync_record_id"], _ROW["id"])


if __name__ == "__main__":
    unittest.main()
