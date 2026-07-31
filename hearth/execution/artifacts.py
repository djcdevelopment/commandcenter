"""Immutable, content-addressed result artifacts.

The bytes live outside SQLite. ``artifact.recorded`` events carry the returned
metadata, making the ledger's artifact index a rebuildable projection.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path
from typing import Any, Mapping, Optional

from .ids import new_artifact_id
from .model import utc_now


class ArtifactStoreError(RuntimeError):
    """Raised when immutable artifact bytes fail validation or persistence."""


def default_artifact_dir() -> Path:
    configured = os.environ.get("HEARTH_ARTIFACT_DIR")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[1] / "var" / "execution" / "artifacts"


class ArtifactStore:
    def __init__(self, root: Optional[Path | str] = None) -> None:
        self.root = Path(root).resolve() if root is not None else default_artifact_dir()
        self.objects = self.root / "objects"
        self.objects.mkdir(parents=True, exist_ok=True)

    def _path_for_digest(self, digest: str) -> Path:
        if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
            raise ArtifactStoreError("sha256 must be 64 lowercase hexadecimal characters")
        return self.objects / digest[:2] / digest

    def put(
        self,
        content: bytes | str,
        *,
        media_type: str = "text/plain; charset=utf-8",
        filename: Optional[str] = None,
        artifact_id: Optional[str] = None,
    ) -> dict[str, Any]:
        if isinstance(content, str):
            encoded = content.encode("utf-8")
        elif isinstance(content, bytes):
            encoded = content
        else:
            raise TypeError("artifact content must be bytes or str")
        if not media_type:
            raise ArtifactStoreError("media_type must not be empty")
        if filename is not None:
            safe_name = Path(filename).name
            if safe_name != filename or not safe_name:
                raise ArtifactStoreError("filename must be a plain basename")

        digest = hashlib.sha256(encoded).hexdigest()
        destination = self._path_for_digest(digest)
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            if destination.stat().st_size != len(encoded):
                raise ArtifactStoreError(f"existing object size mismatch for sha256:{digest}")
        else:
            descriptor, temporary_name = tempfile.mkstemp(
                prefix=".artifact-", dir=str(destination.parent)
            )
            temporary = Path(temporary_name)
            try:
                with os.fdopen(descriptor, "wb") as stream:
                    stream.write(encoded)
                    stream.flush()
                    os.fsync(stream.fileno())
                try:
                    temporary.replace(destination)
                except FileExistsError:
                    # Another writer won the same content-address race.
                    temporary.unlink(missing_ok=True)
            finally:
                temporary.unlink(missing_ok=True)

        metadata: dict[str, Any] = {
            "artifact_id": artifact_id or new_artifact_id(),
            "sha256": digest,
            "size": len(encoded),
            "media_type": media_type,
            "created_at": utc_now(),
        }
        if filename is not None:
            metadata["filename"] = filename
        return metadata

    def read(self, metadata: Mapping[str, Any]) -> bytes:
        digest = metadata.get("sha256")
        size = metadata.get("size")
        if not isinstance(digest, str):
            raise ArtifactStoreError("artifact metadata requires sha256")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            raise ArtifactStoreError("artifact metadata requires non-negative size")
        path = self._path_for_digest(digest)
        try:
            content = path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactStoreError(f"artifact bytes not found: sha256:{digest}") from exc
        if len(content) != size or hashlib.sha256(content).hexdigest() != digest:
            raise ArtifactStoreError(f"artifact integrity check failed: sha256:{digest}")
        return content

    def verify(self, metadata: Mapping[str, Any]) -> bool:
        try:
            self.read(metadata)
        except ArtifactStoreError:
            return False
        return True
