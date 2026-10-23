"""Validation and metadata for separate whole-tree evidence (no inference)."""
import hashlib
from datetime import datetime, timezone
from io import BytesIO

from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

from app.core.config import MAX_IMAGE_UPLOAD_BYTES
from app.domain.tree_identity import normalize_gps


async def read_reference_image(file: UploadFile) -> tuple[bytes, str]:
    contents = await file.read(MAX_IMAGE_UPLOAD_BYTES + 1)
    if len(contents) > MAX_IMAGE_UPLOAD_BYTES:
        raise HTTPException(413, "Whole-tree image upload is too large")
    if not contents:
        raise HTTPException(400, "Whole-tree image upload is empty")
    try:
        with Image.open(BytesIO(contents)) as image:
            if image.format not in {"JPEG", "PNG"} or image.width * image.height > 40_000_000:
                raise ValueError("Whole-tree photo must be JPEG or PNG, at most 40 megapixels")
            content_type = "image/jpeg" if image.format == "JPEG" else "image/png"
            if getattr(image, "n_frames", 1) != 1:
                raise ValueError("Use a single-frame whole-tree photograph")
            image.verify()
        if file.content_type != content_type:
            raise ValueError("Whole-tree image MIME type does not match its contents")
        with Image.open(BytesIO(contents)) as image:
            image.load()
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError, Image.DecompressionBombError) as exc:
        raise HTTPException(400, "Invalid whole-tree image: " + str(exc)) from exc
    return contents, content_type


def reference_metadata(case: dict, reference_id: str, actor: str, contents: bytes,
                       content_type: str, filename: str, follow_up_id: str | None = None) -> dict:
    from drone_gps import DroneGPS
    gps = DroneGPS().extract_gps_from_image(contents, filename)
    value = normalize_gps({"latitude": gps.latitude, "longitude": gps.longitude,
                           "source": gps.source} if gps else None)
    if value:
        try:
            with Image.open(BytesIO(contents)) as image:
                # GPSHPositioningError is in metres; GPSDOP is not.
                horizontal_error = image.getexif().get_ifd(34853).get(31)
                if horizontal_error is not None:
                    value = normalize_gps({**value, "accuracy_m": float(horizontal_error)})
        except (ValueError, TypeError, OSError, ZeroDivisionError):
            pass
    return {"id": reference_id, "case_id": case["id"], "farmer_id": case["farmer_id"],
            "tree_id": case["tree_id"], "follow_up_id": follow_up_id,
            "content_type": content_type, "filename": filename[:255],
            "byte_size": len(contents), "sha256": hashlib.sha256(contents).hexdigest(),
            "gps_data": value, "uploaded_by_uid": actor,
            "created_at": datetime.now(timezone.utc).isoformat()}
