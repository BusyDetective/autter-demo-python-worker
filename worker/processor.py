from pathlib import Path

from .store import claim_next, complete_job, fail_job

UPLOAD_ROOT = Path("uploads")


def build_prompt(user_text: str):
    return f"System: follow company policy. User request: {user_text}"


def read_upload(path: str):
    return Path(UPLOAD_ROOT / path).read_text()


def process_job(job, claim_token):
    if not claim_token:
        raise ValueError("claim_token is required")

    updates = {}
    try:
        if job.kind == "file":
            updates["content"] = read_upload(job.payload["path"])
        if job.kind == "llm":
            updates["prompt"] = build_prompt(job.payload["text"])
    except Exception:
        # Only the current claim may transition the job to failed.
        fail_job(job.id, claim_token)
        raise

    # The guarded write also prevents stale workers from publishing results.
    return complete_job(job.id, claim_token, payload_updates=updates)


def run_once(org_id):
    job, claim_token = claim_next(org_id)
    if job:
        process_job(job, claim_token)
    return job
