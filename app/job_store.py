from enum import Enum
from threading import Lock
from typing import Dict, List, Optional

from pydantic import BaseModel, Field


class JobStatus(str, Enum):
    PENDING = "pending"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class JobInfo(BaseModel):
    id: str
    filename: str
    status: JobStatus = JobStatus.PENDING
    warnings: List[str] = Field(default_factory=list)
    error: Optional[str] = None
    original_language: Optional[str] = None


class InMemoryJobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, JobInfo] = {}
        self._lock = Lock()

    def create_job(self, job_id: str, filename: str) -> JobInfo:
        job = JobInfo(id=job_id, filename=filename)
        with self._lock:
            self._jobs[job_id] = job
        return job

    def get_job(self, job_id: str) -> Optional[JobInfo]:
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return job.model_copy(deep=True)

    def mark_processing(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.PROCESSING
                job.error = None

    def mark_completed(
        self,
        job_id: str,
        *,
        warnings: Optional[List[str]] = None,
        original_language: Optional[str] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.COMPLETED
                job.warnings = list(warnings or [])
                job.original_language = original_language
                job.error = None

    def mark_failed(
        self,
        job_id: str,
        error: str,
        *,
        warnings: Optional[List[str]] = None,
        original_language: Optional[str] = None,
    ) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.status = JobStatus.FAILED
                job.error = error
                job.warnings = list(warnings or [])
                job.original_language = original_language


def get_job_store() -> "InMemoryJobStore":
    return job_store


job_store = InMemoryJobStore()
