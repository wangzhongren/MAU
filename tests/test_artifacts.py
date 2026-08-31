import pytest

from mau_flow import ArtifactRef, LocalArtifactStore


def test_artifact_roundtrip_and_integrity(tmp_path):
    store = LocalArtifactStore(tmp_path)
    ref = store.put_text("reports/result.txt", "ok", "result")
    assert store.read(ref) == b"ok"
    (tmp_path / "reports" / "result.txt").write_text("no")
    with pytest.raises(ValueError, match="integrity"):
        store.read(ref)


def test_artifact_rejects_path_escape(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    with pytest.raises(ValueError, match="escapes"):
        store.put_text("../outside.txt", "no")


def test_artifact_read_rejects_forged_external_reference(tmp_path):
    store = LocalArtifactStore(tmp_path / "store")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    forged = ArtifactRef(
        uri=secret.resolve().as_uri(),
        sha256="0" * 64,
        size_bytes=6,
    )
    with pytest.raises(ValueError, match="escapes"):
        store.read(forged, verify=False)


def test_artifact_read_checks_declared_size(tmp_path):
    store = LocalArtifactStore(tmp_path)
    ref = store.put_text("result.txt", "ok")
    invalid = ref.model_copy(update={"size_bytes": 3})
    with pytest.raises(ValueError, match="size"):
        store.read(invalid)
