import threading
import time
import uuid
from dataclasses import dataclass, field


DEFAULT_LEASE_SECONDS = 1

@dataclass
class Job:
    id: str
    org_id: str
    kind: str
    payload: dict
    status: str = "queued"
    attempts: int = 0
    error: str | None = None
    visible_at: float = 0
    idempotency_key: str | None = None
    claim_token: str | None = field(default=None, repr=False)


jobs: list[Job] = []
_lock = threading.Lock()


def submit_job(org_id, kind, payload, idempotency_key=None):
    job = Job(
        id=str(uuid.uuid4()),
        org_id=org_id,
        kind=kind,
        payload=payload,
        idempotency_key=idempotency_key,
    )
    with _lock:
        jobs.append(job)
    return job


def claim_next(org_id, lease_seconds=None):
    """Atomically claim one visible job and return its job/token pair."""
    lease = DEFAULT_LEASE_SECONDS if lease_seconds is None else lease_seconds
    if lease < 0:
        raise ValueError("lease_seconds must be non-negative")

    with _lock:
        now = time.time()
        for job in jobs:
            if job.org_id != org_id:
                continue

            if job.status == "processing" and job.visible_at <= now:
                # Reclaiming supersedes the previous worker's claim.
                job.status = "queued"
                job.claim_token = None

            if job.status == "queued" and job.visible_at <= now:
                job.status = "processing"
                job.visible_at = now + lease
                job.attempts += 1
                job.claim_token = str(uuid.uuid4())
                return job, job.claim_token

    return None, None


def next_job(org_id):
    """Compatibility wrapper; workers should prefer claim_next()."""
    job, _token = claim_next(org_id)
    return job


def complete_job(job_id, claim_token, payload_updates=None):
    with _lock:
        for job in jobs:
            if job.id == job_id:
                if job.status != "processing" or not claim_token or job.claim_token != claim_token:
                    return False
                if payload_updates:
                    job.payload.update(payload_updates)
                job.status = "complete"
                job.claim_token = None
                return True
    return False


def fail_job(job_id, claim_token, error="worker failed"):
    with _lock:
        for job in jobs:
            if job.id == job_id:
                if job.status != "processing" or not claim_token or job.claim_token != claim_token:
                    return False
                job.status = "failed"
                job.error = error
                job.claim_token = None
                return True
    return False


def get_job(job_id):
    with _lock:
        return next((job for job in jobs if job.id == job_id), None)
