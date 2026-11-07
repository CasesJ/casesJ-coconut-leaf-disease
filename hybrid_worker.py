"""Outbound HTTPS polling worker for CoconutAI hybrid inference (Stage 2C)."""
from __future__ import annotations

import argparse
import json
import logging
import os
import threading
import time
from pathlib import Path
from tempfile import TemporaryDirectory

import requests
import cv2

# A worker receives only its own private environment.  It must not read the
# web application's .env, which can contain Firebase account credentials.
os.environ.setdefault("COCONUTAI_WORKER_PROCESS", "1")

from app.core.config import (
    HYBRID_WORKER_LEASE_SECONDS, UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD,
    UPLOAD_RECORD_CONFIDENCE_THRESHOLD,
)
from app.services.hybrid_source import HybridSourceError, SourceIntegrityError, download_verified_source
from app.services.hybrid_worker_auth import make_worker_headers
from app.infrastructure.cloudinary_storage import CloudinaryStorageError

logger = logging.getLogger("coconutai.hybrid_worker")


class WorkerProtocolError(RuntimeError):
    pass


class WorkerClient:
    def __init__(self, api_url: str, worker_id: str, secret: str, timeout: int = 90):
        self.api_url = api_url.rstrip("/")
        self.worker_id, self.secret, self.timeout = worker_id, secret, timeout
        if not self.api_url.startswith("https://"):
            raise ValueError("Hybrid worker API URL must use HTTPS")
        if not worker_id or not secret:
            raise ValueError("Hybrid worker ID and secret are required")

    def request(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        headers = make_worker_headers(self.secret, self.worker_id, "POST", path, body)
        try:
            response = requests.post(self.api_url + path, data=body, headers=headers,
                                     timeout=(5, self.timeout))
        except requests.RequestException as error:
            raise WorkerProtocolError("Render worker coordinator is unavailable") from error
        if response.status_code == 409:
            raise WorkerProtocolError("Worker lease is no longer valid")
        if response.status_code >= 400:
            raise WorkerProtocolError("Render worker coordinator rejected the request")
        try:
            decoded = response.json()
        except ValueError as error:
            raise WorkerProtocolError("Render worker coordinator returned invalid JSON") from error
        if not isinstance(decoded, dict):
            raise WorkerProtocolError("Render worker coordinator returned invalid JSON")
        return decoded

    def claim(self) -> dict | None:
        return self.request("/hybrid/worker/claim", {}).get("job")

    def renew(self, job_id: str, lease_token: str) -> None:
        self.request(f"/hybrid/worker/jobs/{job_id}/renew", {"lease_token": lease_token})

    def complete(self, job_id: str, lease_token: str, result: dict) -> None:
        self.request(f"/hybrid/worker/jobs/{job_id}/complete", {"lease_token": lease_token, "result": result})

    def fail(self, job_id: str, lease_token: str, category: str, retryable: bool) -> None:
        self.request(f"/hybrid/worker/jobs/{job_id}/fail", {
            "lease_token": lease_token, "category": category, "retryable": retryable,
        })

    def reconcile(self) -> int:
        response = self.request("/hybrid/worker/reconcile", {})
        return int(response.get("reconciled") or 0)


class LeaseKeepalive:
    """Renews a claim while inference runs; never submits after lease loss."""
    def __init__(self, client: WorkerClient, job_id: str, lease_token: str, lease_seconds: int):
        self.client, self.job_id, self.lease_token = client, job_id, lease_token
        self.interval = max(10, lease_seconds // 3)
        self.lost = threading.Event()
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self) -> None:
        while not self.stop.wait(self.interval):
            try:
                self.client.renew(self.job_id, self.lease_token)
            except Exception:
                logger.warning("Hybrid worker lease renewal unavailable job_id=%s", self.job_id)
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=2)


def _encode_hybrid_thumbnail(image) -> bytes:
    """Encode a bounded display derivative from the already-decoded local image."""
    height, width = image.shape[:2]
    scale = min(1.0, 480 / max(height, width))
    thumbnail = image
    if scale < 1.0:
        thumbnail = cv2.resize(
            image, (max(1, round(width * scale)), max(1, round(height * scale))),
            interpolation=cv2.INTER_AREA,
        )
    try:
        ok, encoded = cv2.imencode(".jpg", thumbnail, [cv2.IMWRITE_JPEG_QUALITY, 78])
        if not ok:
            raise ValueError("thumbnail encode failed")
        return encoded.tobytes()
    finally:
        if thumbnail is not image:
            del thumbnail


def _run_existing_pipeline(job: dict, source_path: Path) -> dict:
    """Use the existing decode/inference functions unchanged after verification."""
    from app.api.detections import _map_detections_to_source, _prepare_upload  # noqa: PLC0415
    from app.services.inference_service import run_image_inference  # noqa: PLC0415
    from model import detector  # noqa: PLC0415

    contents = source_path.read_bytes()  # Hash/length were already verified while streamed.
    (inference_contents, image_metadata, lat, lng, gps_source, gps_accuracy,
     image, dimensions, scale_x, scale_y, timings) = _prepare_upload(
        contents, job["public_id"], f"hybrid-{job['id']}",
    )
    del inference_contents
    result = run_image_inference(detector, image, UPLOAD_DISPLAY_CONFIDENCE_THRESHOLD, annotate=False)
    detections = _map_detections_to_source(result["detections"], scale_x, scale_y)
    recorded = [item for item in detections if item["confidence"] >= UPLOAD_RECORD_CONFIDENCE_THRESHOLD]
    # This is a display-only derivative.  It reuses the local inference raster
    # and never changes source bytes, detector preprocessing, or box mapping.
    thumbnail = _encode_hybrid_thumbnail(image) if recorded else None
    del image, result, contents
    # Stage 2C stores a private job result only. A later approved stage will
    # create detection records/evidence with the established Render workflow.
    return {
        "schema": "hybrid-inference-result-v1",
        "detections": detections,
        "recordable_detection_count": len(recorded),
        "source_dimensions": dimensions,
        "gps": {"latitude": lat, "longitude": lng, "accuracy": gps_accuracy, "source": gps_source},
        "image_metadata": image_metadata,
        "inference_backend": detector.backend,
        "source_identity": {"public_id": job["public_id"], "sha256": job["source_sha256"], "bytes": job["source_bytes"]},
        "diagnostics": {"decode_scale": max(scale_x, scale_y), "preparation_ms": timings},
        "thumbnail_bytes": thumbnail,
    }


def process_one(client: WorkerClient) -> bool:
    job = client.claim()
    if not job:
        return False
    job_id = job["id"]
    lease_token = job["lease_token"]
    with LeaseKeepalive(client, job_id, lease_token, HYBRID_WORKER_LEASE_SECONDS) as lease:
        try:
            with TemporaryDirectory(prefix="coconutai-hybrid-") as directory:
                download_started = time.monotonic()
                source = download_verified_source(
                    job["cloudinary_asset"], job["source_sha256"], job["source_bytes"], Path(directory),
                )
                download_ms = (time.monotonic() - download_started) * 1000
                if lease.lost.is_set():
                    return True
                inference_started = time.monotonic()
                result = _run_existing_pipeline(job, source)
                result.setdefault("diagnostics", {})["worker_download_ms"] = round(download_ms, 1)
                result["diagnostics"]["worker_inference_total_ms"] = round((time.monotonic() - inference_started) * 1000, 1)
                thumbnail = result.pop("thumbnail_bytes", None)
                if thumbnail:
                    from app.infrastructure.cloudinary_storage import upload_hybrid_thumbnail  # noqa: PLC0415
                    result["thumbnail_asset"] = upload_hybrid_thumbnail(thumbnail, job_id)
            if lease.lost.is_set():
                return True
            client.complete(job_id, lease_token, result)
            logger.info("Hybrid worker completed job_id=%s detections=%d", job_id, len(result["detections"]))
        except SourceIntegrityError:
            if not lease.lost.is_set():
                client.fail(job_id, lease_token, "source_integrity", False)
        except HybridSourceError:
            if not lease.lost.is_set():
                client.fail(job_id, lease_token, "cloudinary", True)
        except CloudinaryStorageError:
            if not lease.lost.is_set():
                client.fail(job_id, lease_token, "cloudinary", True)
        except WorkerProtocolError:
            # The lease may have expired during a Render sleep/outage. Do not
            # submit another result; recovery will make the job available.
            logger.warning("Hybrid worker lost coordinator contact job_id=%s", job_id)
        except Exception:
            logger.exception("Hybrid worker inference failed job_id=%s", job_id)
            if not lease.lost.is_set():
                client.fail(job_id, lease_token, "inference", True)
    return True


def main() -> None:
    parser = argparse.ArgumentParser(description="CoconutAI outbound hybrid inference worker")
    parser.add_argument("--api-url", default=os.getenv("HYBRID_RENDER_API_URL", ""))
    parser.add_argument("--worker-id", default=os.getenv("HYBRID_WORKER_ID", ""))
    parser.add_argument("--poll-seconds", type=float, default=float(os.getenv("HYBRID_WORKER_POLL_SECONDS", "10")))
    parser.add_argument("--reconcile-seconds", type=float,
                        default=float(os.getenv("HYBRID_WORKER_RECONCILE_SECONDS", "60")))
    parser.add_argument("--request-timeout-seconds", type=int,
                        default=int(os.getenv("HYBRID_WORKER_REQUEST_TIMEOUT_SECONDS", "90")))
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    secret = os.getenv("HYBRID_WORKER_SHARED_SECRET", "")
    if (args.poll_seconds < 1 or args.reconcile_seconds < 10 or args.request_timeout_seconds < 10
            or not args.api_url or not args.worker_id or not secret):
        raise SystemExit("Set HTTPS API URL, worker ID, secret, and poll interval >= 1 second")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    client = WorkerClient(args.api_url, args.worker_id, secret, timeout=args.request_timeout_seconds)
    last_reconcile = 0.0
    retry_delay = args.poll_seconds
    while True:
        try:
            if time.monotonic() - last_reconcile >= args.reconcile_seconds:
                reconciled = client.reconcile()
                if reconciled:
                    logger.info("Hybrid worker reconciled completed_jobs=%d", reconciled)
                last_reconcile = time.monotonic()
            processed = process_one(client)
        except WorkerProtocolError:
            logger.warning("Hybrid worker poll failed; retrying in %.0f seconds", retry_delay)
            processed = False
            if args.once:
                return
            time.sleep(retry_delay)
            retry_delay = min(60.0, retry_delay * 2)
            continue
        if args.once:
            return
        retry_delay = args.poll_seconds
        time.sleep(1 if processed else args.poll_seconds)


if __name__ == "__main__":
    main()
