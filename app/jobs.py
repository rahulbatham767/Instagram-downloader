"""Background download jobs with progress (used for YouTube, where the server
must download + merge/convert before the file can be sent)."""

from __future__ import annotations

import os
import secrets
import shutil
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, Optional

from .extractor import ExtractionError

MAX_CONCURRENT = int(os.environ.get("MAX_CONCURRENT_JOBS", "2"))
JOB_TTL = 30 * 60  # finished files are kept this long, then deleted

_executor = ThreadPoolExecutor(max_workers=MAX_CONCURRENT, thread_name_prefix="job")
_jobs: dict[str, "Job"] = {}
_lock = threading.Lock()


@dataclass
class Job:
    id: str
    filename: str
    workdir: str
    status: str = "queued"  # queued | running | done | error
    progress: float = 0.0
    stage: str = "Waiting in queue"
    error: Optional[str] = None
    path: Optional[str] = None
    created: float = field(default_factory=time.time)

    def public(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "progress": round(self.progress, 3),
            "stage": self.stage,
            "error": self.error,
            "file_url": f"/api/jobs/{self.id}/file" if self.status == "done" else None,
        }


def _sweep() -> None:
    now = time.time()
    with _lock:
        old = [j for j in _jobs.values() if now - j.created > JOB_TTL and j.status in ("done", "error")]
        for j in old:
            _jobs.pop(j.id, None)
    for j in old:
        shutil.rmtree(j.workdir, ignore_errors=True)


def submit(work: Callable[[str, Callable[[float, str], None]], str], filename: str) -> Job:
    """Run `work(workdir, progress_cb) -> file_path` in the background."""
    _sweep()
    job = Job(id=secrets.token_urlsafe(10), filename=filename, workdir=tempfile.mkdtemp(prefix="igdl_job_"))
    with _lock:
        _jobs[job.id] = job

    def progress(frac: float, stage: str) -> None:
        job.progress, job.stage = frac, stage

    def run() -> None:
        job.status, job.stage = "running", "Starting"
        try:
            job.path = work(job.workdir, progress)
            job.status, job.progress, job.stage = "done", 1.0, "Ready"
        except ExtractionError as exc:
            job.status, job.error = "error", str(exc)
        except Exception as exc:  # unexpected; surface something useful
            job.status, job.error = "error", f"Download failed: {exc}"
        if job.status == "error":
            shutil.rmtree(job.workdir, ignore_errors=True)

    _executor.submit(run)
    return job


def get(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def running_count() -> int:
    return sum(1 for j in list(_jobs.values()) if j.status in ("queued", "running"))
