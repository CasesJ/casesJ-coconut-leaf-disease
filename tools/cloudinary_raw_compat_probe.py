"""One-shot approved Cloudinary raw-byte compatibility probe.

Creates exactly one isolated disposable JPEG and never deletes any asset.
Run manually only with the explicit user approval recorded in this chat.
"""
from __future__ import annotations

import hashlib
import json
import time
import sys
from pathlib import Path
from uuid import uuid4

import piexif
import requests
from PIL import Image, ImageCms

from app.core import config
from app.infrastructure import cloudinary_storage


PREFIX = "neuralcoco/hybrid-compat-test"
RUNS_DIRECTORY = Path("tmp") / "cloudinary-compat-runs"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _make_disposable_jpeg(path: Path) -> dict:
    """Generate synthetic metadata only; no farmer or DJI source is read."""
    xmp = (
        b"http://ns.adobe.com/xap/1.0/\x00"
        b'<x:xmpmeta xmlns:x="adobe:ns:meta/" xmlns:dji="http://www.dji.com/drone-dji/1.0/">'
        b'<rdf:RDF><rdf:Description dji:TestMarker="coconutai-compat"/></rdf:RDF></x:xmpmeta>'
    )
    exif = piexif.dump({
        "0th": {piexif.ImageIFD.Make: b"CoconutAI-Test", piexif.ImageIFD.Model: b"Disposable"},
        "Exif": {piexif.ExifIFD.DateTimeOriginal: b"2026:11:07 00:00:00"},
        "GPS": {
            piexif.GPSIFD.GPSLatitudeRef: b"N",
            piexif.GPSIFD.GPSLatitude: ((7, 1), (18, 1), (0, 1)),
            piexif.GPSIFD.GPSLongitudeRef: b"E",
            piexif.GPSIFD.GPSLongitude: ((125, 1), (36, 1), (0, 1)),
        },
        "1st": {}, "thumbnail": None,
    })
    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    image = Image.new("RGB", (37, 29), color=(31, 127, 83))
    image.save(path, "JPEG", quality=91, exif=exif, xmp=xmp, icc_profile=icc)
    original = path.read_bytes()
    return {"bytes": original, "xmp": xmp, "icc": icc}


def _metadata_present(value: bytes, marker: bytes, icc: bytes) -> dict:
    with Image.open(__import__("io").BytesIO(value)) as image:
        exif = image.getexif()
        gps = exif.get_ifd(piexif.ImageIFD.GPSTag) if piexif.ImageIFD.GPSTag in exif else {}
        return {
            "exif_make": exif.get(piexif.ImageIFD.Make) == "CoconutAI-Test",
            "gps_present": bool(gps),
            "xmp_marker": marker in value,
            "icc_exact": image.info.get("icc_profile") == icc,
        }


def _metadata_bytes(value: bytes) -> dict:
    with Image.open(__import__("io").BytesIO(value)) as image:
        return {
            "exif": image.info.get("exif", b""),
            "xmp": image.info.get("xmp", b""),
            "icc_profile": image.info.get("icc_profile", b""),
        }


def main() -> None:
    if not all((config.CLOUDINARY_CLOUD_NAME, config.CLOUDINARY_API_KEY, config.CLOUDINARY_API_SECRET)):
        raise RuntimeError("Cloudinary is not configured")
    resume = "--resume" in sys.argv[1:]
    public_id = f"{PREFIX}/{uuid4().hex}.jpg"
    context = "hybrid_compat_test=true|purpose=raw-byte-preservation"
    run_id = uuid4().hex
    run_directory = RUNS_DIRECTORY / run_id
    run_directory.mkdir(parents=True, exist_ok=False)
    original_path = run_directory / "original.jpg"
    downloaded_path = run_directory / "downloaded.jpg"
    try:
        generated = _make_disposable_jpeg(original_path)
        original = generated["bytes"]
        pre_upload = {
            "run_id": run_id, "public_id": public_id, "sha256": _sha256(original),
            "bytes": len(original),
            "metadata": _metadata_present(original, b"coconutai-compat", generated["icc"]),
        }
        (run_directory / "pre-upload.json").write_text(json.dumps(pre_upload, sort_keys=True))
        cloudinary_storage._configure()
        from cloudinary import api, uploader, utils  # noqa: PLC0415

        if resume:
            listing = api.resources(
                resource_type="raw", type="authenticated", prefix=f"{PREFIX}/", max_results=10,
            )
            matches = listing.get("resources") if isinstance(listing, dict) else None
            if not isinstance(matches, list) or len(matches) != 1:
                raise RuntimeError("Expected exactly one isolated compatibility asset; refusing to select one")
            public_id = str(matches[0].get("public_id") or "")
            if not public_id.startswith(f"{PREFIX}/"):
                raise RuntimeError("Refusing to inspect an asset outside the test-only prefix")
            resource = api.resource(public_id, resource_type="raw", type="authenticated", context=True)
            asset_id = str(resource.get("asset_id") or "")
        else:
            result = uploader.upload(
                str(original_path), resource_type="raw", type="authenticated", public_id=public_id,
                overwrite=False, unique_filename=False, use_filename=False,
                context=context, return_error=True,
            )
            if not isinstance(result, dict) or result.get("error"):
                raise RuntimeError("Cloudinary rejected the isolated raw upload")
            asset_id = str(result.get("asset_id") or "")
            resource = api.resource(public_id, resource_type="raw", type="authenticated", context=True)
        expires = int(time.time()) + 120
        url = utils.private_download_url(
            public_id, str(resource.get("format") or "jpg"), resource_type="raw",
            type="authenticated", expires_at=expires,
        )
        response = requests.get(url, timeout=(5, 30), allow_redirects=False)
        response.raise_for_status()
        downloaded = response.content
        downloaded_path.write_bytes(downloaded)
        original_metadata = _metadata_bytes(original)
        downloaded_metadata = _metadata_bytes(downloaded)
        exact_match = original == downloaded
        inspection = {
            "public_id_matches": resource.get("public_id") == public_id,
            "asset_id_matches": resource.get("asset_id") == asset_id,
            "resource_type_raw": resource.get("resource_type") == "raw",
            "authenticated": resource.get("type") == "authenticated",
            "byte_count_matches": int(resource.get("bytes") or -1) == len(original),
            "context": resource.get("context"),
        }
        report = {
            "run_directory": str(run_directory),
            "public_id": public_id,
            "uploaded_sha256": _sha256(original),
            "downloaded_sha256": _sha256(downloaded),
            "sha256_matches": _sha256(original) == _sha256(downloaded),
            "uploaded_bytes": len(original), "downloaded_bytes": len(downloaded),
            "byte_count_matches": len(original) == len(downloaded),
            "byte_for_byte_matches": exact_match,
            "uploaded_metadata": _metadata_present(original, b"coconutai-compat", generated["icc"]),
            "downloaded_metadata": _metadata_present(downloaded, b"coconutai-compat", generated["icc"]),
            "metadata_bytes_match": {
                key: original_metadata[key] == downloaded_metadata[key]
                for key in original_metadata
            },
            "admin_inspection": inspection,
            "test_asset_retained": True,
            "comparison_source": "regenerated deterministic disposable fixture" if resume else "original upload bytes",
        }
        (run_directory / "report.json").write_text(json.dumps(report, sort_keys=True))
        print(json.dumps(report, sort_keys=True))
    except Exception as error:
        (run_directory / "failure.json").write_text(json.dumps({
            "run_id": run_id, "public_id": public_id, "error": type(error).__name__,
            "message": str(error)[:300],
        }, sort_keys=True))
        raise


if __name__ == "__main__":
    main()
