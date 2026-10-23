"""
sts_job_manager.py — runs one table run's transfer job and waits for it.

The job-level "done" only means the job ran; per-object failures are in the
operation's counters / error_breakdowns, so success requires every object
found to have been copied (or already present), no failures, and at least the
number of files this run uploaded.
"""
import time

from google.cloud import storage_transfer_v1 as storagetransfer

from src.common import log
from src.sts.sts_client import StsClient

LOG = log.get("sts")


class StsJobManager:
    def __init__(self, config: dict, client=None, sleep=time.sleep):
        self.config = config
        self.client = client or storagetransfer.StorageTransferServiceClient()
        self.sts_client = StsClient(config, client=self.client)
        sts_cfg = config.get("sts") or {}
        self.poll_interval_seconds = int(sts_cfg.get("poll_interval_seconds", 15))
        self.timeout_seconds = int(sts_cfg.get("timeout_seconds", 3600))
        self.delete_jobs = bool(sts_cfg.get("delete_jobs_after_run", True))
        self._sleep = sleep

    def transfer_file(self, rel_dir: str, name: str, description: str = "") -> dict:
        """Copies ONE staged object (rel_dir + name) to the same path in GCS."""
        job_name = self.sts_client.create_job(rel_dir, description, include=[name])
        result = {"status": "ERROR", "job_name": job_name}
        try:
            result = self._run_and_wait(job_name, 1)
            return result
        finally:
            if self.delete_jobs and result.get("status") != "TIMEOUT":
                self.sts_client.delete_job(job_name)

    def transfer_directory(self, rel_dir: str, expected_files: int, description: str = "") -> dict:
        job_name = self.sts_client.create_job(rel_dir, description)
        result = {"status": "ERROR", "job_name": job_name}
        try:
            result = self._run_and_wait(job_name, expected_files)
            return result
        finally:
            if self.delete_jobs and result.get("status") != "TIMEOUT":
                self.sts_client.delete_job(job_name)

    def _run_and_wait(self, job_name: str, expected_files: int) -> dict:
        operation = self.client.run_transfer_job(request=storagetransfer.RunTransferJobRequest(
            job_name=job_name, project_id=self.config["gcp"]["project_id"]))
        waited = 0
        while waited < self.timeout_seconds:
            if operation.done():
                operation.result()                    # raises if the job itself failed
                try:
                    summary = self._summarize(job_name, operation.metadata, expected_files)
                except AttributeError as e:
                    LOG.warning("Could not read transfer counters for %s: %s", job_name, e)
                    summary = {"job_name": job_name, "status": "SUCCESS", "warning": str(e)}
                LOG.info("Transfer %s: %s (found=%s copied=%s skipped=%s failed=%s)", job_name, summary["status"],
                         summary.get("objects_found"), summary.get("objects_copied"),
                         summary.get("objects_skipped"), summary.get("objects_failed"))
                return summary
            self._sleep(self.poll_interval_seconds)
            waited += self.poll_interval_seconds
        LOG.error("Transfer %s timed out after %ss (job kept for inspection)", job_name, self.timeout_seconds)
        return {"job_name": job_name, "status": "TIMEOUT"}

    @staticmethod
    def _summarize(job_name: str, transfer_operation, expected_files: int) -> dict:
        c = transfer_operation.counters
        found, copied = c.objects_found_from_source, c.objects_copied_to_sink
        skipped, failed = c.objects_from_source_skipped_by_sync, c.objects_from_source_failed
        errors = []
        for eb in transfer_operation.error_breakdowns:
            detail = None
            if eb.error_log_entries and eb.error_log_entries[0].error_details:
                detail = eb.error_log_entries[0].error_details[0]
            errors.append({"error_code": getattr(eb.error_code, "name", str(eb.error_code)),
                           "count": eb.error_count, "sample_detail": detail})
        ok = (copied + skipped) >= found and found >= expected_files and failed == 0 and not errors
        return {"job_name": job_name, "status": "SUCCESS" if ok else "PARTIAL_FAILURE", "objects_found": found,
                "objects_copied": copied, "objects_skipped": skipped, "objects_failed": failed, "errors": errors}
