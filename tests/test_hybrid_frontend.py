"""Static checks for the dormant hybrid browser branch."""
from pathlib import Path


SOURCE = (Path(__file__).parents[1] / "static" / "app.js").read_text(encoding="utf-8")
HTML = (Path(__file__).parents[1] / "static" / "index.html").read_text(encoding="utf-8")


def test_hybrid_browser_feature_is_explicitly_disabled_by_default():
    assert "hybridUploads: false" in HTML
    assert "hybridUploadFeatureEnabled = false" in SOURCE
    assert "accountInfo?.features?.hybrid_uploads === true" in SOURCE
    assert ": detectImage(files[index]" in SOURCE


def test_hybrid_browser_uses_signed_capability_without_secrets_or_raw_urls():
    assert "/hybrid/uploads/intents" in SOURCE
    assert "upload_parameters" in SOURCE and "cloudAsset.asset_id" in SOURCE
    assert "HYBRID_WORKER_SHARED_SECRET" not in SOURCE
    assert "CLOUDINARY_API_SECRET" not in SOURCE
    assert "private_download_url" not in SOURCE


def test_hybrid_browser_avoids_duplicate_direct_upload_after_ambiguous_outcome():
    assert "hybridUploadTransfers" in SOURCE
    assert "uploadOutcomeUnknown" in SOURCE
    assert "Do not upload this file again" in SOURCE
    assert "HYBRID_POLL_TIMEOUT_MS" in SOURCE
