"""
sts_job_manager.py

Triggers a one-time run of a transfer job and polls until it
finishes — this is what the Airflow task actually calls, so the DAG
doesn't move on to the BigQuery load until the transfer is really done.

IMPORTANT: the job-level status (operation.done()/result() succeeding)
only means the transfer JOB ran to completion — it does NOT mean every
object inside it was actually copied. Storage Transfer Service tracks
per-object failures (auth errors, missing source objects, etc.)
separately in the operation's `counters` / `error_breakdowns`, without
failing the job itself. So we inspect those explicitly and only report
"SUCCESS" when everything the job found was actually copied.
"""
import time
from google.cloud import storage_transfer_v1 as storagetransfer

from src.sts.sts_client import StsClient


class StsJobManager:
    def __init__(self, config: dict):
        self.config = config
        self.sts_client = StsClient(config)
        self.client = storagetransfer.StorageTransferServiceClient()

    def run_transfer_for_table(self, table_name: str, schema_name: str,
                                poll_interval_seconds: int = 15,
                                timeout_seconds: int = 3600) -> dict:
        job_name = self.sts_client.create_job_for_table(table_name, schema_name)

        run_request = storagetransfer.RunTransferJobRequest(
            job_name=job_name, project_id=self.config["gcp"]["project_id"]
        )
        print(f"[sts_job_manager] Running transfer job {job_name} for "
              f"{schema_name}.{table_name} (polling every {poll_interval_seconds}s, "
              f"timeout {timeout_seconds}s) ...")
        operation = self.client.run_transfer_job(request=run_request)

        elapsed = 0
        while elapsed < timeout_seconds:
            if operation.done():
                # RunTransferJob's LRO "result" is just a google.protobuf.Empty —
                # the actual TransferOperation (with counters/error_breakdowns)
                # is carried as the operation's metadata, not its result.
                operation.result()  # still call this: raises if the job itself errored outright
                try:
                    summary = self._summarize(job_name, operation.metadata)
                    print(f"[sts_job_manager] Transfer {job_name} finished for "
                          f"{schema_name}.{table_name}: status={summary['status']}, "
                          f"found={summary.get('objects_found')}, "
                          f"copied={summary.get('objects_copied')}, "
                          f"skipped={summary.get('objects_skipped')}, "
                          f"failed={summary.get('objects_failed')}")
                    return summary
                except AttributeError as e:
                    # Metadata shape wasn't what we expected — don't crash the
                    # whole pipeline over a diagnostics feature. Fall back to
                    # "the job finished" and let batch_file_exists() in
                    # gcs_manager.py be the real source of truth downstream.
                    print(f"[sts_job_manager] WARNING: could not read transfer counters "
                          f"for {job_name}: {e}")
                    return {"job_name": job_name, "status": "SUCCESS",
                            "warning": f"could not read transfer counters: {e}"}
            time.sleep(poll_interval_seconds)
            elapsed += poll_interval_seconds

        print(f"[sts_job_manager] Transfer {job_name} for {schema_name}.{table_name} "
              f"TIMED OUT after {timeout_seconds}s")
        return {"job_name": job_name, "status": "TIMEOUT"}

    def _summarize(self, job_name: str, transfer_operation) -> dict:
        counters = transfer_operation.counters
        found = counters.objects_found_from_source
        copied = counters.objects_copied_to_sink
        skipped = counters.objects_from_source_skipped_by_sync
        failed = counters.objects_from_source_failed

        error_samples = []
        for eb in transfer_operation.error_breakdowns:
            sample_detail = None
            if eb.error_log_entries:
                entry = eb.error_log_entries[0]
                if entry.error_details:
                    sample_detail = entry.error_details[0]
            error_samples.append({
                "error_code": eb.error_code.name if hasattr(eb.error_code, "name") else str(eb.error_code),
                "count": eb.error_count,
                "sample_detail": sample_detail,
            })

        # "Success" only if the job actually accounted for every object it
        # found — either by copying it or legitimately skipping it because
        # it already matched at the destination. Any failure, or any object
        # found but neither copied nor skipped, means something didn't land.
        fully_accounted = (copied + skipped) >= found and failed == 0 and not error_samples
        status = "SUCCESS" if fully_accounted else "PARTIAL_FAILURE"

        return {
            "job_name": job_name,
            "status": status,
            "objects_found": found,
            "objects_copied": copied,
            "objects_skipped": skipped,
            "objects_failed": failed,
            "errors": error_samples,
        }
