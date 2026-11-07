from datetime import datetime, timedelta, timezone
import threading
import pytest
from app.services.hybrid_jobs import *

class Snap:
    def __init__(self, value): self.value=value; self.exists=value is not None
    def to_dict(self): return dict(self.value)
class Ref:
    def __init__(self, db, path): self.db,self.path=db,path
class Collection:
    def __init__(self, db, name): self.db,self.name=db,name
    def document(self, ident): return Ref(self.db, f"{self.name}/{ident}")
class Tx:
    def __init__(self, db): self.db=db; self.read_versions={}; self.writes=[]
    def get(self, ref):
        with self.db.lock:
            if isinstance(ref, Collection):
                values=[]
                for key,value in self.db.data.items():
                    if key.startswith(ref.name+"/"):
                        self.read_versions[key]=self.db.versions[key]; values.append(Snap(value))
                return values
            self.read_versions[ref.path]=self.db.versions.get(ref.path)
            return Snap(self.db.data.get(ref.path))
    def create(self, ref, value):
        self.writes.append(("create",ref.path,dict(value)))
    def update(self, ref, value): self.writes.append(("update",ref.path,dict(value)))
    def commit(self):
        # Only the first pair of competing commits wait; retry attempts proceed.
        barrier=self.db.barrier
        if barrier and self.db.barrier_calls < 2:
            with self.db.lock: self.db.barrier_calls += 1
            barrier.wait(timeout=5)
        with self.db.lock:
            if any(self.db.versions.get(path) != version for path,version in self.read_versions.items()):
                raise RetryableTransactionError("optimistic transaction conflict")
            for kind,path,value in self.writes:
                if kind == "create":
                    if path in self.db.data: raise RetryableTransactionError("create conflict")
                    self.db.data[path]=value
                else: self.db.data[path].update(value)
                self.db.versions[path]=self.db.versions.get(path,0)+1
class DB:
    def __init__(self, barrier=None): self.data={}; self.versions={}; self.lock=threading.Lock(); self.barrier=barrier; self.barrier_calls=0
    def collection(self,name): return Collection(self,name)
    def transaction(self): return Tx(self)

def store(): return HybridJobStore(DB(), now=lambda: datetime(2026,1,1,tzinfo=timezone.utc))
def args(**more):
    value = {"user_id":"u", "idempotency_key":"a"*16,
             "public_id":"neuralcoco/hybrid-original/"+"b"*32,
             "source_sha256":"c"*64, "source_bytes":10, "route_type":"detection"}
    value.update(more)
    return value
def test_idempotency_and_conflict():
    s=store(); job,created=s.create_upload_intent(**args()); assert created
    assert s.create_upload_intent(**args()) == (job,False)
    with pytest.raises(IdempotencyConflict): s.create_upload_intent(**args(source_bytes=11))
def test_leases_completion_and_invalid_transition():
    s=store(); job,_=s.create_upload_intent(**args()); s.mark_verified(job); claimed=s.claim("w",60); assert claimed["id"]==job
    s.complete(job,"w",claimed["lease_token"],{"ok":True})
    with pytest.raises(InvalidTransition): s.mark_cleanup_pending(job)
def test_expired_lease_recovers():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]; s=HybridJobStore(DB(),now=lambda:now[0]); job,_=s.create_upload_intent(**args());s.mark_verified(job);s.claim("w",1);now[0]+=timedelta(seconds=2);assert s.recover_expired_leases()==[job]


def test_repeated_expired_leases_stop_at_existing_retry_limit():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]
    s=HybridJobStore(DB(),now=lambda:now[0],max_retries=3)
    job,_=s.create_upload_intent(**args());s.mark_verified(job)
    for attempt in range(4):
        assert s.claim("w",1)["id"] == job
        now[0]+=timedelta(seconds=2)
        assert s.recover_expired_leases() == [job]
        assert s.get_job(job)["retry_count"] == attempt + 1
    assert s.get_job(job)["status"] == FAILED
    assert s.claim("other",1) is None

def test_two_workers_cannot_complete_same_job_after_reclaim():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]; s=HybridJobStore(DB(), now=lambda:now[0])
    job,_=s.create_upload_intent(**args()); s.mark_verified(job)
    first = s.claim("worker-one", 1)
    assert first["lease_owner"] == "worker-one"
    now[0] += timedelta(seconds=2); s.recover_expired_leases()
    second = s.claim("worker-two", 60)
    assert second["lease_owner"] == "worker-two"
    with pytest.raises(LeaseConflict): s.complete(job, "worker-one", first["lease_token"], {"old": True})
    assert s.complete(job, "worker-two", second["lease_token"], {"ok": True})["status"] == COMPLETED

def test_users_have_independent_idempotency_namespaces_and_retry_limit():
    s=store(); one,_=s.create_upload_intent(**args(user_id="one")); two,_=s.create_upload_intent(**args(user_id="two")); assert one != two
    s.mark_verified(one)
    for _ in range(3):
        claim = s.claim("w", 60); assert s.fail(one, "w", claim["lease_token"], True)["status"] == RETRYABLE_FAILED
    claim = s.claim("w", 60); assert s.fail(one, "w", claim["lease_token"], True)["status"] == FAILED

def test_renewal_rejects_expired_lease():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]; s=HybridJobStore(DB(),now=lambda:now[0]); job,_=s.create_upload_intent(**args());s.mark_verified(job);claim=s.claim("w",1);now[0]+=timedelta(seconds=2)
    with pytest.raises(LeaseConflict): s.renew_lease(job,"w",claim["lease_token"],30)


def test_expired_worker_cannot_complete_before_reclaim():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]; s=HybridJobStore(DB(),now=lambda:now[0])
    job,_=s.create_upload_intent(**args()); s.mark_verified(job); claim=s.claim("w",1); now[0]+=timedelta(seconds=2)
    with pytest.raises(LeaseConflict): s.complete(job,"w",claim["lease_token"],{"stale":True})


def test_same_worker_id_cannot_use_an_old_lease_token_after_reclaim():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]
    s=HybridJobStore(DB(),now=lambda:now[0])
    job,_=s.create_upload_intent(**args()); s.mark_verified(job)
    first=s.claim("laptop",1)
    now[0]+=timedelta(seconds=2); s.recover_expired_leases()
    second=s.claim("laptop",60)
    assert first["lease_token"] != second["lease_token"]
    with pytest.raises(LeaseConflict): s.renew_lease(job,"laptop",first["lease_token"],60)
    with pytest.raises(LeaseConflict): s.fail(job,"laptop",first["lease_token"],True)
    with pytest.raises(LeaseConflict): s.complete(job,"laptop",first["lease_token"],{"stale":True})
    assert s.get_job(job)["status"] == LEASED
    assert s.renew_lease(job,"laptop",second["lease_token"],60)["lease_token"] == second["lease_token"]
    completed=s.complete(job,"laptop",second["lease_token"],{"ok":True})
    assert completed["status"] == COMPLETED
    assert s.complete(job,"laptop",second["lease_token"],{"ok":True})["status"] == COMPLETED
    with pytest.raises(LeaseConflict): s.complete(job,"laptop",first["lease_token"],{"ok":True})

def test_simultaneous_workers_claim_once_and_retry_conflict():
    barrier=threading.Barrier(2); db=DB(); s=HybridJobStore(db)
    job,_=s.create_upload_intent(**args()); s.mark_verified(job)
    db.barrier=barrier
    results=[]
    def claim(worker): results.append((worker,s.claim(worker,60)))
    threads=[threading.Thread(target=claim,args=(name,)) for name in ("one","two")]
    [t.start() for t in threads]; [t.join(5) for t in threads]
    assert all(not t.is_alive() for t in threads)
    claimed=[item for _,item in results if item is not None]
    assert len(claimed)==1 and claimed[0]["lease_owner"] in {"one","two"}
    assert db.data[f"{JOB_COLLECTION}/{job}"]["lease_owner"] == claimed[0]["lease_owner"]

def test_concurrent_idempotency_reservation_creates_one_job():
    barrier=threading.Barrier(2); db=DB(barrier); s=HybridJobStore(db)
    results=[]
    def reserve(): results.append(s.create_upload_intent(**args()))
    a,b=threading.Thread(target=reserve),threading.Thread(target=reserve);a.start();b.start();a.join(5);b.join(5)
    assert len(results)==2 and len({job for job,_ in results})==1
    assert sum(created for _,created in results)==1


def test_upload_intent_expiry_quota_and_verified_confirmation():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]
    s=HybridJobStore(DB(), now=lambda:now[0])
    job,_=s.create_upload_intent(**args(), upload_expires_at=now[0]+timedelta(seconds=10), daily_limit=1)
    with pytest.raises(UploadQuotaExceeded):
        s.create_upload_intent(**args(idempotency_key="b"*16), upload_expires_at=now[0]+timedelta(seconds=10), daily_limit=1)
    asset={"provider":"cloudinary", "public_id":args()["public_id"], "asset_id":"asset", "byte_size":10}
    confirmed=s.confirm_verified_upload(job, "u", asset)
    assert confirmed["status"] == PENDING and confirmed["cloudinary_asset"] == asset
    assert s.confirm_verified_upload(job, "u", asset)["status"] == PENDING
    with pytest.raises(HybridJobError): s.get_upload_intent(job, "another-user")


def test_expired_upload_is_cleanup_pending_and_cannot_confirm():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]
    s=HybridJobStore(DB(), now=lambda:now[0])
    job,_=s.create_upload_intent(**args(), upload_expires_at=now[0]+timedelta(seconds=1))
    now[0]+=timedelta(seconds=2)
    with pytest.raises(UploadIntentExpired):
        s.confirm_verified_upload(job, "u", {"public_id":args()["public_id"]})
    assert s.get_upload_intent(job, "u")["status"] == CLEANUP_PENDING


def test_worker_nonce_is_atomic_and_replay_is_rejected():
    now=[datetime(2026,1,1,tzinfo=timezone.utc)]
    s=HybridJobStore(DB(), now=lambda:now[0])
    s.consume_worker_nonce("nonce-123", now[0]+timedelta(seconds=60))
    with pytest.raises(LeaseConflict):
        s.consume_worker_nonce("nonce-123", now[0]+timedelta(seconds=60))
