"""Retain remote URLs and every local-image fallback after short-circuiting stat."""
from pathlib import Path

import pytest

from app.domain import detection


@pytest.mark.parametrize("original_exists,annotated_exists", [(False, False), (True, False), (False, True), (True, True)])
@pytest.mark.parametrize("url_kind", ["remote", "missing", "stale_local", "empty"])
def test_image_defaults_preserve_remote_and_legacy_fallbacks(
    monkeypatch, tmp_path, original_exists, annotated_exists, url_kind
):
    original_dir, annotated_dir = tmp_path / "original", tmp_path / "annotated"
    original_dir.mkdir()
    annotated_dir.mkdir()
    monkeypatch.setattr(detection, "UPLOAD_IMAGE_DIR", original_dir)
    monkeypatch.setattr(detection, "ANNOTATED_IMAGE_DIR", annotated_dir)
    if original_exists:
        (original_dir / "sample.jpg").write_bytes(b"fixture")
    if annotated_exists:
        (annotated_dir / "sample.jpg").write_bytes(b"fixture")
    record = {"id": "sample", "detections": []}
    if url_kind == "remote":
        record.update(image_url="https://example.test/original.jpg",
                      annotated_image_url="https://example.test/annotated.jpg")
    elif url_kind == "stale_local":
        record.update(image_url="/static/uploads/absent.jpg",
                      annotated_image_url="/static/annotated_uploads/absent.jpg")
    elif url_kind == "empty":
        record.update(image_url="", annotated_image_url="")
    calls = []
    exists = Path.exists

    def counted(path):
        calls.append(path)
        return exists(path)

    monkeypatch.setattr(Path, "exists", counted)
    result = detection.apply_record_defaults(record)
    if url_kind == "remote":
        assert result["image_url"] == "https://example.test/original.jpg"
        assert result["annotated_image_url"] == "https://example.test/annotated.jpg"
        assert calls == []
    else:
        expected_annotated = "/static/annotated_uploads/sample.jpg" if annotated_exists else None
        expected_original = "/static/uploads/sample.jpg" if original_exists else expected_annotated
        assert (result.get("image_url") or None) == expected_original
        assert (result.get("annotated_image_url") or None) == expected_annotated
        assert len(calls) == 2
