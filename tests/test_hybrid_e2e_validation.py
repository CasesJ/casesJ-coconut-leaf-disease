"""Stage 2F local control-plane validation; no Firebase/Cloudinary network."""
import shutil
from pathlib import Path
from uuid import uuid4

import hybrid_worker


def _job():
    return {
        "id": "a" * 32, "user_id": "farmer", "route_type": "detection",
        "public_id": "neuralcoco/hybrid-original/" + "b" * 32 + ".jpg",
        "source_sha256": "c" * 64, "source_bytes": 12,
        "lease_token": "d" * 32,
        "cloudinary_asset": {"resource_type": "raw", "delivery_type": "authenticated"},
    }


class Client:
    def __init__(self, job): self.job=job; self.completed=[]; self.failed=[]; self.renewed=[]
    def claim(self): value,self.job=self.job,None; return value
    def renew(self, job_id, lease_token): self.renewed.append((job_id,lease_token))
    def complete(self, job_id, lease_token, result): self.completed.append((job_id,lease_token,result))
    def fail(self, job_id, lease_token, category, retryable): self.failed.append((job_id,lease_token,category,retryable))


class WorkspaceTemporaryDirectory:
    def __init__(self, *args, **kwargs):
        self.path=Path("tmp") / f"stage2f-worker-{uuid4().hex}"
    def __enter__(self): self.path.mkdir(parents=True); return str(self.path)
    def __exit__(self, *args): shutil.rmtree(self.path)


def test_worker_end_to_end_submits_once_and_removes_source_temp(monkeypatch):
    # Use the workspace fixture directory supplied by pytest only for a fake
    # verified source; no Cloudinary or model invocation is involved here.
    client=Client(_job())
    monkeypatch.setattr(hybrid_worker, "TemporaryDirectory", WorkspaceTemporaryDirectory)
    observed={}
    def download(_asset, _hash, _bytes, directory):
        observed["directory"]=Path(directory)
        path=Path(directory)/"verified.jpg"; path.write_bytes(b"fixture"); return path
    monkeypatch.setattr(hybrid_worker, "download_verified_source", download)
    monkeypatch.setattr(hybrid_worker, "_run_existing_pipeline", lambda job, path: {
        "detections":[{"class":"Bud Rot","confidence":0.9,"bbox":[1,2,3,4]}], "diagnostics":{},
    })
    assert hybrid_worker.process_one(client) is True
    assert len(client.completed) == 1 and not client.failed
    assert client.completed[0][0] == "a"*32
    assert client.completed[0][1] == "d"*32
    assert not observed["directory"].exists()
    assert hybrid_worker.process_one(client) is False


def test_worker_coordinator_loss_never_submits_or_retries_stale_result(monkeypatch):
    client=Client(_job())
    monkeypatch.setattr(hybrid_worker, "TemporaryDirectory", WorkspaceTemporaryDirectory)
    monkeypatch.setattr(hybrid_worker, "download_verified_source", lambda *args: Path(__file__))
    def lost(*args): raise hybrid_worker.WorkerProtocolError("lease lost")
    monkeypatch.setattr(hybrid_worker, "_run_existing_pipeline", lost)
    assert hybrid_worker.process_one(client) is True
    assert not client.completed and not client.failed
