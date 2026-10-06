import datetime
import json
import os
import uuid
from google.cloud import storage as gcs_storage

try:
    from google.cloud import bigquery
except ImportError:
    bigquery = None


class MetadataManager:
    def __init__(self, logging_config: dict, gcp_config: dict | None = None):
        self.gcp_config = gcp_config or {}
        raw_log_path = logging_config.get("raw_log_path")
        if not raw_log_path:
            bucket = self.gcp_config.get("gcs_bucket")
            if not bucket:
                raise ValueError(
                    "logging.raw_log_path is not set and gcp.gcs_bucket is "
                    "unavailable to default it — pass the gcp config or set "
                    "raw_log_path explicitly."
                )
            raw_log_path = f"gcs://{bucket}/pipeline_logs/raw"
        self.raw_log_path = raw_log_path
        self._is_gcs = self.raw_log_path.startswith("gcs://")

        self.write_to_bq = logging_config.get("write_logs_to_bigquery", True) and bigquery is not None
        self._bq_client = None
        self._log_table_ref = None
        if self.write_to_bq:
            project_id = self.gcp_config.get("project_id")
            dataset = self.gcp_config.get("bq_dataset")
            log_table = (self.gcp_config.get("log_table")
                         or logging_config.get("bq_log_table")
                         or "migration_pipeline_logs")
            if project_id and dataset:
                self._bq_client = bigquery.Client(project=project_id)
                self._log_table_ref = f"{project_id}.{dataset}.{log_table}"
            else:
                self.write_to_bq = False

    def start_run(self) -> str:
        """Call once per DAG run. Returns a run_id shared by every stage."""
        return str(uuid.uuid4())

    def log_stage_start(self, run_id: str, table_name: str, stage: str, schema_name: str = None) -> None:
        self._write_event({
            "run_id": run_id,
            "table_name": table_name,
            "schema_name": schema_name,
            "stage": stage,
            "status": "STARTED",
            "batch_index": None,
            "rows_processed": None,
            "bytes_processed": None,
            "started_at": datetime.datetime.utcnow().isoformat(),
            "finished_at": None,
            "error_message": None,
            "extra": {},
        })

    def log_stage_end(self, run_id: str, table_name: str, stage: str, status: str,
                       schema_name: str = None, batch_index: int = None,
                       rows_processed: int = None, bytes_processed: int = None,
                       error_message: str = None, extra: dict = None) -> None:
        """status should be 'SUCCESS' or 'FAILED'."""
        self._write_event({
            "run_id": run_id,
            "table_name": table_name,
            "schema_name": schema_name,
            "stage": stage,
            "status": status,
            "batch_index": batch_index,
            "rows_processed": rows_processed,
            "bytes_processed": bytes_processed,
            "started_at": None,
            "finished_at": datetime.datetime.utcnow().isoformat(),
            "error_message": error_message,
            "extra": extra or {},
        })

    def _write_event(self, event: dict) -> None:
        run_id = event["run_id"]
        if self._is_gcs:
            self._write_gcs_event(run_id, event)
        else:
            self._write_local_event(run_id, event)

        if self.write_to_bq:
            self._write_bq_event(event)

    def _write_local_event(self, run_id: str, event: dict) -> None:
        directory = os.path.join(self.raw_log_path, run_id)
        os.makedirs(directory, exist_ok=True)
        path = os.path.join(directory, f"{uuid.uuid4()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(event, f)

    def _write_gcs_event(self, run_id: str, event: dict) -> None:
        bucket_name, prefix = self.raw_log_path[len("gcs://"):].split("/", 1)
        client = gcs_storage.Client()
        blob_name = f"{prefix.rstrip('/')}/{run_id}/{uuid.uuid4()}.json"
        client.bucket(bucket_name).blob(blob_name).upload_from_string(
            json.dumps(event), content_type="application/json"
        )

    def _write_bq_event(self, event: dict) -> None:
        row = {
            "run_id": event["run_id"],
            "table_name": event["table_name"],
            "schema_name": event.get("schema_name"),
            "stage": event["stage"],
            "status": event["status"],
            "batch_index": event.get("batch_index"),
            "rows_processed": event.get("rows_processed"),
            "bytes_processed": event.get("bytes_processed"),
            "started_at": event.get("started_at"),
            "finished_at": event.get("finished_at"),
            "error_message": event.get("error_message"),
            "extra_json": json.dumps(event.get("extra") or {}),
        }
        try:
            errors = self._bq_client.insert_rows_json(self._log_table_ref, [row])
            if errors:
                print(f"[metadata_manager] WARNING: BigQuery log insert failed: {errors}")
        except Exception as e:
            # Logging must never take down the pipeline itself.
            print(f"[metadata_manager] WARNING: BigQuery log insert raised: {e}")
