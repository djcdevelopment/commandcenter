"""Media-root containment tests.

This is a security boundary, so the cases that matter are the adversarial ones.
A test suite that only proves valid paths resolve would pass against a resolver
that permitted everything.
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hearth.toolsurface import _media_scope as media


class MediaScopeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._temp = tempfile.TemporaryDirectory()
        self.root = Path(self._temp.name) / "BF6-Highlights"
        for sub in media.READABLE_SUBTREES:
            (self.root / sub).mkdir(parents=True, exist_ok=True)
        patcher = patch.dict(os.environ, {media.MEDIA_ROOT_ENV: str(self.root)})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self._temp.cleanup)

    # ---------------------------------------------------------------- allowed

    def test_resolves_a_relative_path_in_each_subtree(self) -> None:
        for sub in media.READABLE_SUBTREES:
            resolved = media.resolve_media("%s/session/file.mkv" % sub)
            self.assertTrue(str(resolved).startswith(str(self.root.resolve())))

    def test_accepts_forward_and_back_slashes(self) -> None:
        # The request sidecar is written by Linux and read by Windows.
        a = media.resolve_media("raw/session/file.mkv")
        b = media.resolve_media("raw\\session\\file.mkv")
        self.assertEqual(a, b)

    def test_resolves_a_target_that_does_not_exist_yet(self) -> None:
        # Render outputs are judged before they are created.
        resolved = media.resolve_media("drafts/s/clip-horizontal.mp4", mode="write")
        self.assertFalse(resolved.exists())
        self.assertTrue(str(resolved).startswith(str(self.root.resolve())))

    # ----------------------------------------------------------- raw is r/o

    def test_raw_is_readable(self) -> None:
        self.assertTrue(media.resolve_media("raw/s/f.mkv", mode="read"))

    def test_write_against_raw_is_refused(self) -> None:
        # AM4 gets this from a read-only cifs mount; OMEN owns E: natively and
        # has no such protection, so the resolver supplies it.
        with self.assertRaises(media.MediaPathError) as caught:
            media.resolve_media("raw/s/f.mkv", mode="write")
        self.assertIn("read-only", str(caught.exception))

    def test_writable_subtrees_accept_writes(self) -> None:
        for sub in media.WRITABLE_SUBTREES:
            self.assertTrue(media.resolve_media("%s/s/f.mp4" % sub, mode="write"))

    # ------------------------------------------------------------- refusals

    def test_traversal_is_refused(self) -> None:
        for bad in (
            "raw/../../../Windows/System32/cmd.exe",
            "work/../../etc/passwd",
            "raw/..",
        ):
            with self.assertRaises(media.MediaPathError, msg=bad):
                media.resolve_media(bad)

    def test_unc_is_refused_explicitly(self) -> None:
        # Not merely "fails containment" -- refused as a category, so that a
        # future root change cannot quietly make UNC reachable.
        for bad in (
            r"\\server\share\evil.mkv",
            "//server/share/evil.mkv",
            r"\\?\UNC\server\share\evil.mkv",
        ):
            with self.assertRaises(media.MediaPathError, msg=bad) as caught:
                media.resolve_media(bad)
            self.assertIn("UNC", str(caught.exception))

    def test_absolute_path_outside_the_root_is_refused(self) -> None:
        for bad in (r"C:\Windows\System32\cmd.exe", r"D:\somewhere\else.mkv"):
            with self.assertRaises(media.MediaPathError, msg=bad):
                media.resolve_media(bad)

    def test_drive_relative_path_is_refused(self) -> None:
        # 'C:foo' resolves against the process CWD for that drive -- ambiguous.
        with self.assertRaises(media.MediaPathError):
            media.resolve_media("C:raw/session/file.mkv")

    def test_unknown_subtree_is_refused(self) -> None:
        for bad in ("logs/omen-agent.log", "etc/passwd", "nope/x.mkv"):
            with self.assertRaises(media.MediaPathError, msg=bad):
                media.resolve_media(bad)

    def test_empty_and_non_string_are_refused(self) -> None:
        for bad in ("", "   ", None, 5, []):
            with self.assertRaises(media.MediaPathError):
                media.resolve_media(bad)  # type: ignore[arg-type]

    def test_bad_mode_is_refused(self) -> None:
        with self.assertRaises(media.MediaPathError):
            media.resolve_media("work/a/b", mode="append")

    @unittest.skipUnless(os.name == "nt", "junction creation is Windows-only")
    def test_junction_escaping_the_root_is_refused(self) -> None:
        # The resolver follows reparse points BEFORE judging containment, so a
        # junction planted inside the root cannot be used to reach outside it.
        outside = Path(self._temp.name) / "outside"
        outside.mkdir(parents=True, exist_ok=True)
        (outside / "loot.txt").write_text("secret", encoding="utf-8")
        link = self.root / "work" / "escape"
        result = os.system('mklink /J "%s" "%s" >nul 2>&1' % (link, outside))
        if result != 0 or not link.exists():
            self.skipTest("could not create a junction in this environment")
        with self.assertRaises(media.MediaPathError):
            media.resolve_media("work/escape/loot.txt")

    # ------------------------------------------------------------- helpers

    def test_subtree_of_reports_the_first_component(self) -> None:
        self.assertEqual("raw", media.subtree_of("raw/session/file.mkv"))
        self.assertEqual("drafts", media.subtree_of("drafts\\s\\c.mp4"))

    def test_relative_to_media_round_trips(self) -> None:
        resolved = media.resolve_media("drafts/s/clip-vertical.mp4", mode="write")
        self.assertEqual(
            "drafts/s/clip-vertical.mp4", media.relative_to_media(resolved)
        )

    def test_relative_to_media_refuses_a_path_outside_the_root(self) -> None:
        # Guards the sidecar boundary: AM4 must never receive an OMEN-absolute
        # path, and must never be handed one from outside the media root.
        with self.assertRaises(media.MediaPathError):
            media.relative_to_media(Path(self._temp.name) / "outside" / "x.mp4")

    def test_does_not_consult_hearth_scope(self) -> None:
        # The whole point of a separate authority: clearing HEARTH_SCOPE must
        # not change media resolution, and setting it must not widen it.
        with patch.dict(os.environ, {"HEARTH_SCOPE": str(Path(self._temp.name))}):
            with self.assertRaises(media.MediaPathError):
                media.resolve_media(r"C:\Windows\System32\cmd.exe")
            self.assertTrue(media.resolve_media("raw/s/f.mkv"))


if __name__ == "__main__":
    unittest.main()
