"""
Tests for tools/workflow/check_doc_claims.py (Stream B2).

Coverage:
- Registry parses (valid JSON, expected fields present)
- Checker passes on current repo state (with the waiver in place)
- A synthetic failing claim without a waiver → nonzero exit code
- An expired waiver → FAIL (not WAIVED)
- A list-valued path compares LENGTH, not the list object
"""

import io
import json
import sys
import tempfile
import unittest
from datetime import date
from pathlib import Path

# Allow imports from repo root regardless of where tests are run from.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from tools.workflow.check_doc_claims import (
    _active_waivers,
    _evaluate,
    _resolve_path,
    run_checks,
    CLAIMS_FILE,
    WAIVERS_FILE,
    _REPO_ROOT,
)


class TestRegistryParses(unittest.TestCase):
    """The registry and waivers files are valid JSON with the right shape."""

    def test_claims_file_exists_and_is_list(self):
        self.assertTrue(CLAIMS_FILE.exists(), f"{CLAIMS_FILE} missing")
        with open(CLAIMS_FILE) as fh:
            claims = json.load(fh)
        self.assertIsInstance(claims, list)
        self.assertGreater(len(claims), 0)

    def test_each_claim_has_required_fields(self):
        with open(CLAIMS_FILE) as fh:
            claims = json.load(fh)
        required_top = {"claim_id", "doc", "description", "check"}
        required_check = {"file", "path", "op"}
        for c in claims:
            missing_top = required_top - c.keys()
            self.assertFalse(missing_top, f"claim missing top-level fields: {missing_top}")
            missing_check = required_check - c["check"].keys()
            self.assertFalse(missing_check, f"claim check missing fields: {missing_check}")

    def test_waivers_file_exists_and_is_list(self):
        self.assertTrue(WAIVERS_FILE.exists(), f"{WAIVERS_FILE} missing")
        with open(WAIVERS_FILE) as fh:
            waivers = json.load(fh)
        self.assertIsInstance(waivers, list)

    def test_each_waiver_has_required_fields(self):
        with open(WAIVERS_FILE) as fh:
            waivers = json.load(fh)
        required = {"claim_id", "reason", "author", "created", "expires"}
        for w in waivers:
            missing = required - w.keys()
            self.assertFalse(missing, f"waiver missing fields: {missing}")

    def test_known_claim_ids_present(self):
        with open(CLAIMS_FILE) as fh:
            claims = json.load(fh)
        ids = {c["claim_id"] for c in claims}
        self.assertIn("roadmap-first-capability", ids)
        self.assertIn("candidates-present", ids)


class TestCheckerOnCurrentRepo(unittest.TestCase):
    """Checker exits 0 on the current repo state (waiver covers the failing capability claim)."""

    def test_checker_exits_zero(self):
        out = io.StringIO()
        rc = run_checks(out=out)
        self.assertEqual(rc, 0, f"checker returned nonzero. output:\n{out.getvalue()}")

    def test_roadmap_capability_is_waived(self):
        out = io.StringIO()
        run_checks(out=out)
        output = out.getvalue()
        self.assertIn("roadmap-first-capability", output)
        # Should be WAIVED, not FAIL
        lines = [l for l in output.splitlines() if "roadmap-first-capability" in l]
        self.assertTrue(lines, "roadmap-first-capability not found in output")
        self.assertIn("WAIVED", lines[0])

    def test_candidates_present_passes(self):
        out = io.StringIO()
        run_checks(out=out)
        output = out.getvalue()
        lines = [l for l in output.splitlines() if "candidates-present" in l]
        self.assertTrue(lines, "candidates-present not found in output")
        self.assertIn("PASS", lines[0])


class TestSyntheticFailWithoutWaiver(unittest.TestCase):
    """A failing claim with no waiver causes exit code 1."""

    def _make_claim(self, tmp: Path, claim_id: str, op: str, value, path: str, file_content: dict) -> tuple:
        claims_file = tmp / "claims.json"
        waivers_file = tmp / "waivers.json"
        kfile = tmp / "k.json"
        kfile.write_text(json.dumps(file_content))
        claims_file.write_text(json.dumps([{
            "claim_id": claim_id,
            "doc": "TEST.html",
            "description": "test claim",
            "check": {"file": "k.json", "path": path, "op": op, "value": value},
        }]))
        waivers_file.write_text("[]")
        return claims_file, waivers_file

    def test_failing_claim_no_waiver_exits_nonzero(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            claims_file, waivers_file = self._make_claim(
                tmp,
                claim_id="synthetic-fail",
                op="gte",
                value=100,
                path="count",
                file_content={"count": 0},
            )
            out = io.StringIO()
            rc = run_checks(claims_path=claims_file, waivers_path=waivers_file, repo_root=tmp, out=out)
            self.assertEqual(rc, 1)
            self.assertIn("FAIL", out.getvalue())

    def test_passing_claim_no_waiver_exits_zero(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            claims_file, waivers_file = self._make_claim(
                tmp,
                claim_id="synthetic-pass",
                op="gte",
                value=1,
                path="count",
                file_content={"count": 5},
            )
            out = io.StringIO()
            rc = run_checks(claims_path=claims_file, waivers_path=waivers_file, repo_root=tmp, out=out)
            self.assertEqual(rc, 0)
            self.assertIn("PASS", out.getvalue())


class TestExpiredWaiver(unittest.TestCase):
    """An expired waiver does not prevent a FAIL."""

    def test_expired_waiver_counts_as_fail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            kfile = tmp / "knowledge.json"
            kfile.write_text(json.dumps({"capability_count": 0}))

            claims_file = tmp / "claims.json"
            claims_file.write_text(json.dumps([{
                "claim_id": "expired-test",
                "doc": "TEST.html",
                "description": "test with expired waiver",
                "check": {"file": "knowledge.json", "path": "capability_count", "op": "gte", "value": 1},
            }]))

            waivers_file = tmp / "waivers.json"
            waivers_file.write_text(json.dumps([{
                "claim_id": "expired-test",
                "reason": "was valid last year",
                "author": "test",
                "created": "2025-01-01",
                "expires": "2025-12-31",
            }]))

            out = io.StringIO()
            # Use a today date well after the expiry
            rc = run_checks(
                claims_path=claims_file,
                waivers_path=waivers_file,
                repo_root=tmp,
                today=date(2026, 7, 3),
                out=out,
            )
            self.assertEqual(rc, 1)
            lines = [l for l in out.getvalue().splitlines() if "expired-test" in l]
            self.assertTrue(lines)
            self.assertIn("FAIL", lines[0])

    def test_active_waiver_suppresses_fail(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            kfile = tmp / "knowledge.json"
            kfile.write_text(json.dumps({"capability_count": 0}))

            claims_file = tmp / "claims.json"
            claims_file.write_text(json.dumps([{
                "claim_id": "active-waiver-test",
                "doc": "TEST.html",
                "description": "test with active waiver",
                "check": {"file": "knowledge.json", "path": "capability_count", "op": "gte", "value": 1},
            }]))

            waivers_file = tmp / "waivers.json"
            waivers_file.write_text(json.dumps([{
                "claim_id": "active-waiver-test",
                "reason": "still valid",
                "author": "test",
                "created": "2026-07-01",
                "expires": "2026-12-31",
            }]))

            out = io.StringIO()
            rc = run_checks(
                claims_path=claims_file,
                waivers_path=waivers_file,
                repo_root=tmp,
                today=date(2026, 7, 3),
                out=out,
            )
            self.assertEqual(rc, 0)
            lines = [l for l in out.getvalue().splitlines() if "active-waiver-test" in l]
            self.assertTrue(lines)
            self.assertIn("WAIVED", lines[0])

    def test_never_expires_waiver_always_active(self):
        waivers = [{"claim_id": "x", "reason": "r", "author": "a", "created": "2026-01-01", "expires": None}]
        active = _active_waivers(waivers, date(2099, 1, 1))
        self.assertIn("x", active)


class TestListPathComparesLength(unittest.TestCase):
    """When the resolved path is a list, the checker compares len(list), not the list itself."""

    def test_list_path_compares_length_gte(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            kfile = tmp / "k.json"
            kfile.write_text(json.dumps({"items": ["a", "b", "c"]}))

            claims_file = tmp / "claims.json"
            claims_file.write_text(json.dumps([{
                "claim_id": "list-gte",
                "doc": "TEST.html",
                "description": "list length >= 2",
                "check": {"file": "k.json", "path": "items", "op": "gte", "value": 2},
            }]))
            waivers_file = tmp / "waivers.json"
            waivers_file.write_text("[]")

            out = io.StringIO()
            rc = run_checks(claims_path=claims_file, waivers_path=waivers_file, repo_root=tmp, out=out)
            self.assertEqual(rc, 0)
            lines = [l for l in out.getvalue().splitlines() if "list-gte" in l]
            self.assertTrue(lines)
            self.assertIn("PASS", lines[0])
            # actual column should show the length, not the list
            self.assertIn("3", lines[0])

    def test_list_path_compares_length_fails_when_empty(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            tmp = Path(tmp_dir)
            kfile = tmp / "k.json"
            kfile.write_text(json.dumps({"items": []}))

            claims_file = tmp / "claims.json"
            claims_file.write_text(json.dumps([{
                "claim_id": "list-empty",
                "doc": "TEST.html",
                "description": "list length >= 1",
                "check": {"file": "k.json", "path": "items", "op": "gte", "value": 1},
            }]))
            waivers_file = tmp / "waivers.json"
            waivers_file.write_text("[]")

            out = io.StringIO()
            rc = run_checks(claims_path=claims_file, waivers_path=waivers_file, repo_root=tmp, out=out)
            self.assertEqual(rc, 1)
            lines = [l for l in out.getvalue().splitlines() if "list-empty" in l]
            self.assertTrue(lines)
            self.assertIn("FAIL", lines[0])

    def test_resolve_path_basic(self):
        data = {"a": {"b": {"c": 42}}}
        self.assertEqual(_resolve_path(data, "a.b.c"), 42)

    def test_evaluate_list_gte(self):
        self.assertTrue(_evaluate([1, 2, 3], "gte", 3))
        self.assertFalse(_evaluate([1], "gte", 3))

    def test_evaluate_eq(self):
        self.assertTrue(_evaluate(5, "eq", 5))
        self.assertFalse(_evaluate(5, "eq", 6))

    def test_evaluate_exists(self):
        self.assertTrue(_evaluate("x", "exists", None))
        self.assertFalse(_evaluate(None, "exists", None))

    def test_candidates_present_real_file(self):
        """Verify candidates-present uses list length against the real knowledge file."""
        out = io.StringIO()
        run_checks(out=out)
        output = out.getvalue()
        lines = [l for l in output.splitlines() if "candidates-present" in l]
        self.assertTrue(lines)
        # actual should be numeric (the list length, 15 as of 2026-07-02)
        parts = lines[0].split()
        actual_col = parts[2]  # 0=claim_id, 1=expected, 2=actual, 3=result
        self.assertTrue(actual_col.isdigit(), f"expected numeric actual, got {actual_col!r}")
        self.assertGreater(int(actual_col), 0)


if __name__ == "__main__":
    unittest.main()
