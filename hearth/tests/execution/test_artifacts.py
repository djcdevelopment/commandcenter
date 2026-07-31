from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from hearth.execution import ArtifactStore, ArtifactStoreError


class ArtifactStoreTest(unittest.TestCase):
    def test_put_deduplicates_bytes_but_issues_distinct_artifact_ids(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            first = store.put("complete result", filename="result.md")
            second = store.put("complete result", filename="other.md")
            self.assertNotEqual(first["artifact_id"], second["artifact_id"])
            self.assertEqual(first["sha256"], second["sha256"])
            self.assertEqual(b"complete result", store.read(first))
            self.assertTrue(store.verify(second))
            objects = list((Path(temporary) / "objects").glob("*/*"))
            self.assertEqual(1, len(objects))

    def test_read_detects_integrity_failure(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            metadata = store.put(b"safe")
            object_path = (
                Path(temporary)
                / "objects"
                / metadata["sha256"][:2]
                / metadata["sha256"]
            )
            object_path.write_bytes(b"tampered")
            with self.assertRaisesRegex(ArtifactStoreError, "integrity"):
                store.read(metadata)

    def test_rejects_path_traversal_filename(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = ArtifactStore(Path(temporary))
            with self.assertRaisesRegex(ArtifactStoreError, "basename"):
                store.put("nope", filename="../secret.txt")


if __name__ == "__main__":
    unittest.main()
