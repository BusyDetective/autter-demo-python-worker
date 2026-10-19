from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from .store import get_job, submit_job

app = FastAPI()


class JobIn(BaseModel):
    org_id: str
    kind: str
    payload: dict
    callback_url: str | None = None


def _public(job):
    result = dict(job.__dict__)
    result.pop("claim_token", None)
    return result


@app.post("/jobs")
def create_job(body: JobIn, idempotency_key: str | None = Header(default=None)):
    return _public(submit_job(body.org_id, body.kind, body.payload, idempotency_key))


@app.get("/jobs/{job_id}")
def get_job_endpoint(job_id: str):
    job = get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return _public(job)
