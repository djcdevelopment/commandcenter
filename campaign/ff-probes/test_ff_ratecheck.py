r"""The epoch label must move when the baseline does.

Found live on 2026-09-09: ``--set-baseline`` wrote the new rate, the new config string and
the new note, but never touched ``baseline_epoch``. So after the ``-np 8`` re-baseline the
file carried a 2026-09-09 number under a label naming the 2026-08-29 epoch -- an epoch the
restart had already ended -- and ``hearth.health.rungstate`` served that stale label to every
consumer of the door's health verdict.

That is precisely the failure ADR-0044 exists to prevent: the epoch is the identity of the
reference contract, so a number and a label that disagree make "at_rate" unfalsifiable. These
tests pin the rule that the old label is never carried forward.
"""
from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ff_ratecheck as rc  # noqa: E402

STALE = ("2026-08-29T18:22 incumbent epoch, dual-split asserted by b70tools BDF. "
         "EPOCH-SCOPED IS NOT EPOCH-HOMOGENEOUS: this labels the reference CONTRACT, not a "
         "guarantee that the machine was stationary within it.")


class EpochLabelTests(unittest.TestCase):
    def test_an_explicit_label_is_used_as_given(self):
        out = rc.epoch_label("2026-09-09T07:05:18-07:00 incumbent epoch, -np 8", "ignored", "n")
        self.assertTrue(out.startswith("2026-09-09T07:05:18-07:00 incumbent epoch, -np 8"))

    def test_with_no_explicit_label_one_is_derived_from_the_baselines_own_stamp(self):
        out = rc.epoch_label("", "2026-09-09T07:06:00-07:00", "sat-l1 -np 8 -ub 1024")
        self.assertIn("2026-09-09T07:06:00-07:00 incumbent epoch", out)
        self.assertIn("sat-l1 -np 8 -ub 1024", out)

    def test_the_derived_label_never_reuses_the_previous_one(self):
        # The whole defect: a re-baseline that keeps the old epoch label attaches a new
        # number to an epoch that is over.
        out = rc.epoch_label("", "2026-09-09T07:06:00-07:00", "sat-l1 -np 8")
        self.assertNotIn("2026-08-29", out)
        self.assertIn("2026-09-09", out)

    def test_the_adr_0044_contract_note_survives_a_re_baseline(self):
        # Replacing the identity must not also drop the sentence that stops a shared epoch
        # from being read as a comparability guarantee.
        out = rc.epoch_label("", "2026-09-09T07:06:00-07:00", "")
        self.assertIn("EPOCH-SCOPED IS NOT EPOCH-HOMOGENEOUS", out)
        self.assertIn("NOT thereby comparable", out)

    def test_the_contract_note_is_not_appended_twice(self):
        out = rc.epoch_label(STALE, "2026-09-09T07:06:00-07:00", "")
        self.assertEqual(out.count("EPOCH-SCOPED IS NOT EPOCH-HOMOGENEOUS"), 1)

    def test_a_microsecond_stamp_is_trimmed_but_the_offset_is_kept(self):
        out = rc.epoch_label("", "2026-09-09T07:06:00.482913-07:00", "")
        self.assertIn("2026-09-09T07:06:00 incumbent epoch", out)
        self.assertNotIn("482913", out)


class LiveBaselineFileTests(unittest.TestCase):
    """The live file is data, not a fixture -- but the defect was IN it, so it is checked."""

    def test_the_live_omen_arc_label_names_the_epoch_its_number_was_measured_in(self):
        import io
        import json
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rate-baselines.json")
        rung = json.load(io.open(path, encoding="utf-8"))["rungs"]["omen-arc"]
        stamp = rung["baseline_set"][:10]
        self.assertIn(stamp, rung["baseline_epoch"],
                      "baseline_epoch must name the day the baseline was set, not an "
                      "earlier epoch the restart ended")
        self.assertIn("EPOCH-SCOPED IS NOT EPOCH-HOMOGENEOUS", rung["baseline_epoch"])


if __name__ == "__main__":
    unittest.main()
