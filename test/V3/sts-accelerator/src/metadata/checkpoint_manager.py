"""
checkpoint_manager.py — per-table run state, append-only in the control dataset.

One row per committed chunk (IN_PROGRESS), plus COMPLETED / FAILED, keyed by
(resource_group, source_server, source_database, schema, table, scope_key):

  scope_key  full | incremental | partial:<hash of the filter>
             so a partial reload never makes a full load look COMPLETED, and an
             incremental watermark is never confused with a full-load key.

  last_pk_json          typed JSON of the last committed key (resume point)
  low/high_watermark    the window an incremental run is working on (a resumed
                        run keeps the same window)
  watermark_json        on COMPLETED incremental runs: where the next run starts
"""
import datetime

from google.cloud import bigquery

from src.bigquery.bq_control_tables import source_key_values
from src.bigquery.dataset_naming import control_table_ref
from src.common import values


class CheckpointManager:
    def __init__(self, config: dict, checkpoint_table: str | None = None, client=None):
        self.project_id = config["gcp"]["project_id"]
        name = checkpoint_table or (config.get("metadata") or {}).get("checkpoint_table") or "migration_checkpoint"
        self.table_ref = control_table_ref(config, name)
        self.source_keys = source_key_values(config)
        self.client = client or bigquery.Client(project=self.project_id, location=config["gcp"].get("location"))

    def _latest(self, table_cfg: dict, scope_key: str, status: str | None = None) -> dict | None:
        status_filter = "AND status = @status" if status else ""
        query = f"""
            SELECT status, last_pk_json, last_batch_index, rows_done, low_watermark_json,
                   high_watermark_json, watermark_json, run_id, updated_at
            FROM `{self.table_ref}`
            WHERE table_name = @table_name AND schema_name = @schema_name AND scope_key = @scope_key
              AND resource_group = @resource_group AND source_server = @source_server
              AND source_database = @source_database {status_filter}
            ORDER BY updated_at DESC
            LIMIT 1
        """
        params = [bigquery.ScalarQueryParameter("table_name", "STRING", table_cfg["name"]),
                  bigquery.ScalarQueryParameter("schema_name", "STRING", table_cfg["schema"]),
                  bigquery.ScalarQueryParameter("scope_key", "STRING", scope_key)]
        for key in ("resource_group", "source_server", "source_database"):
            params.append(bigquery.ScalarQueryParameter(key, "STRING", self.source_keys.get(key)))
        if status:
            params.append(bigquery.ScalarQueryParameter("status", "STRING", status))
        rows = list(self.client.query(query, job_config=bigquery.QueryJobConfig(query_parameters=params)).result())
        if not rows:
            return None
        r = rows[0]
        return {
            "status": r.status,
            "last_pk": values.loads(r.last_pk_json),
            "chunk_index": r.last_batch_index,
            "rows_done": r.rows_done or 0,
            "low_watermark": values.load_one(r.low_watermark_json),
            "high_watermark": values.load_one(r.high_watermark_json),
            "watermark": values.load_one(r.watermark_json),
            "run_id": r.run_id,
        }

    def get_state(self, table_cfg: dict) -> dict | None:
        """Latest row of this table's scope (any status)."""
        return self._latest(table_cfg, table_cfg["scope_key"])

    def last_completed(self, table_cfg: dict) -> dict | None:
        return self._latest(table_cfg, table_cfg["scope_key"], status="COMPLETED")

    def write(self, run_id: str, table_cfg: dict, status: str, chunk_index: int | None = None,
              last_pk: dict | None = None, rows_done: int | None = None, low=None, high=None,
              watermark=None) -> None:
        row = {
            **self.source_keys,
            "table_name": table_cfg["name"],
            "schema_name": table_cfg["schema"],
            "scope_key": table_cfg["scope_key"],
            "load_mode": table_cfg["mode"],
            "status": status,
            "last_pk_json": values.dumps(last_pk),
            "last_batch_index": chunk_index,
            "rows_done": rows_done,
            "low_watermark_json": values.dump_one(low),
            "high_watermark_json": values.dump_one(high),
            "watermark_json": values.dump_one(watermark),
            "run_id": run_id,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        errors = self.client.insert_rows_json(self.table_ref, [row])
        if errors:
            raise RuntimeError(f"Failed to write checkpoint for {table_cfg['schema']}.{table_cfg['name']}: {errors}")
