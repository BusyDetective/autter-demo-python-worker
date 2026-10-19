import threading

import pytest

from worker import processor, store
from worker.api import _public
from worker.processor import process_job, run_once
from worker.store import claim_next, complete_job, fail_job, jobs, next_job, submit_job


@pytest.fixture(autouse=True)
def clean_queue(monkeypatch):
    jobs.clear()
    now = {"value": 1000.0}
    monkeypatch.setattr(store.time, "time", lambda: now["value"])
    yield now
    jobs.clear()


def test_queued_job_can_be_claimed():
    job = submit_job("org_a", "llm", {"text": "hello"})
    claimed, token = claim_next("org_a")
    assert claimed is job
    assert token
    assert job.status == "processing"
    assert job.attempts == 1


def test_job_cannot_be_claimed_again_before_lease_expires(clean_queue):
    submit_job("org_a", "llm", {"text": "hello"})
    assert claim_next("org_a", lease_seconds=10)[0] is not None
    clean_queue["value"] += 9.9
    assert claim_next("org_a")[0] is None


def test_expired_claim_gets_new_token(clean_queue):
    job = submit_job("org_a", "llm", {"text": "hello"})
    first_job, first_token = claim_next("org_a", lease_seconds=1)
    assert first_job is job
    clean_queue["value"] += 1.1
    second_job, second_token = claim_next("org_a", lease_seconds=10)
    assert second_job is job
    assert second_token != first_token
    assert job.attempts == 2


def test_stale_worker_cannot_complete_reclaimed_job(clean_queue):
    job = submit_job("org_a", "llm", {"text": "hello"})
    _, old_token = claim_next("org_a", lease_seconds=1)
    clean_queue["value"] += 2
    _, new_token = claim_next("org_a", lease_seconds=10)
    assert complete_job(job.id, old_token, {"prompt": "stale"}) is False
    assert job.status == "processing"
    assert job.payload == {"text": "hello"}
    assert complete_job(job.id, new_token, {"prompt": "fresh"}) is True
    assert job.payload["prompt"] == "fresh"


def test_stale_worker_cannot_fail_reclaimed_job(clean_queue):
    job = submit_job("org_a", "llm", {"text": "hello"})
    _, old_token = claim_next("org_a", lease_seconds=1)
    clean_queue["value"] += 2
    _, new_token = claim_next("org_a", lease_seconds=10)
    assert fail_job(job.id, old_token, "stale failure") is False
    assert job.status == "processing"
    assert job.error is None
    assert fail_job(job.id, new_token, "current failure") is True
    assert job.status == "failed"


def test_process_job_requires_token_and_does_not_mutate_job():
    job = submit_job("org_a", "llm", {"text": "hello"})
    _, token = claim_next("org_a")
    with pytest.raises(TypeError):
        process_job(job)
    with pytest.raises(ValueError):
        process_job(job, None)
    with pytest.raises(ValueError):
        process_job(job, "")
    assert job.status == "processing"
    assert job.claim_token == token
    assert job.payload == {"text": "hello"}


def test_process_job_rejects_wrong_token():
    job = submit_job("org_a", "llm", {"text": "hello"})
    claim_next("org_a")
    assert process_job(job, "not-the-token") is False
    assert job.status == "processing"
    assert "prompt" not in job.payload


def test_run_once_uses_claimed_token_if_job_is_reclaimed_during_processing(monkeypatch, clean_queue):
    job = submit_job("org_a", "llm", {"text": "hello"})
    original_process = processor.process_job

    def reclaim_before_processing(claimed_job, captured_token):
        clean_queue["value"] += 2
        _, new_token = claim_next("org_a", lease_seconds=10)
        assert new_token != captured_token
        return original_process(claimed_job, captured_token)

    monkeypatch.setattr(processor, "process_job", reclaim_before_processing)
    assert run_once("org_a") is job
    assert job.status == "processing"
    assert job.payload == {"text": "hello"}


def test_jobs_from_other_org_are_not_claimed():
    submit_job("org_b", "llm", {"text": "other"})
    assert next_job("org_a") is None


def test_submit_job_returns_job_and_appends_under_lock():
    job = submit_job("org_a", "llm", {"text": "hello"})
    assert job in jobs
    assert job.status == "queued"


def test_api_public_representation_hides_claim_token():
    job = submit_job("org_a", "llm", {"text": "hello"})
    claim_next("org_a")
    assert "claim_token" not in _public(job)


def test_concurrent_claims_have_one_winner_for_single_visible_job():
    job = submit_job("org_a", "llm", {"text": "hello"})
    barrier = threading.Barrier(16)
    results = []
    result_lock = threading.Lock()

    def claim():
        barrier.wait()
        result = claim_next("org_a", lease_seconds=30)
        with result_lock:
            results.append(result)

    threads = [threading.Thread(target=claim) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    claimed = [result for result in results if result[0] is job]
    assert len(claimed) == 1
