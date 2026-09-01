"""Artifact persistence with content integrity checks."""

from __future__ import annotations

import hashlib
import mimetypes
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from .contracts import ArtifactRef


class LocalArtifactStore:
    def __init__(self, root: str | Path):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, name: str, data: bytes, description: str = "") -> ArtifactRef:
        path = self._safe_path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        digest = hashlib.sha256(data).hexdigest()
        media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        return ArtifactRef(
            uri=path.as_uri(),
            sha256=digest,
            description=description,
            media_type=media_type,
            size_bytes=len(data),
        )

    def put_text(self, name: str, text: str, description: str = "") -> ArtifactRef:
        return self.put_bytes(name, text.encode("utf-8"), description)

    def read(self, ref: ArtifactRef, verify: bool = True) -> bytes:
        parsed = urlparse(ref.uri)
        if parsed.scheme != "file" or parsed.netloc not in {"", "localhost"}:
            raise ValueError(f"Unsupported artifact URI: {ref.uri}")
        path = Path(url2pathname(parsed.path)).resolve()
        if path != self.root and self.root not in path.parents:
            raise ValueError(f"Artifact path escapes store root: {ref.uri}")
        data = path.read_bytes()
        if len(data) != ref.size_bytes:
            raise ValueError(f"Artifact size check failed: {ref.uri}")
        if verify and hashlib.sha256(data).hexdigest() != ref.sha256:
            raise ValueError(f"Artifact integrity check failed: {ref.uri}")
        return data

    def _safe_path(self, name: str) -> Path:
        candidate = (self.root / name).resolve()
        if candidate != self.root and self.root not in candidate.parents:
            raise ValueError(f"Artifact path escapes store root: {name}")
        if candidate == self.root:
            raise ValueError("Artifact name must identify a file")
        return candidate
