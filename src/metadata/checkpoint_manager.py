"""
checkpoint_manager.py

This is the failure-recovery strategy: after every batch is loaded to
staging and successfully MERGEd into its target table, we commit a
checkpoint row (last primary key seen, last batch index, run_id) to
the migration_checkpoint BigQuery table. If a table's pipeline run
fails partway through, the checkpoint from its last successful batch
is still sitting there.

On the NEXT cycle, before extracting a table we read its checkpoint:
  - status == "COMPLETED"  -> table finished cleanly last time.
    table_pipeline.run_table() SKIPS a COMPLETED load_mode="full" table
    entirely (no extract / upload / transfer / load) unless force=True
    is passed. Incremental tables are never skipped, because their whole
    purpose is to re-check the source for newly appended rows.
  - status == "FAILED" or "IN_PROGRESS" (a run that crashed mid-table
    never got to write COMPLETED) -> resume extraction starting right
    after last_pk_json instead of from the top, so we don't re-pull
    and re-merge millions of already-migrated rows. Because the load
    path is a MERGE (see bq_merge.py), even re-processing the batch
    that was in flight when it failed is safe — it just overwrites the
    same rows, never duplicates them.
"""
import datetime
import json

from google.cloud import bigquery


class CheckpointManager:
    def __init__(self, config: dict, checkpoint_table: str):
        self.project_id = config["gcp"]["project_id"]
        self.dataset = config["gcp"]["bq_dataset"]
        self.table_ref = f"{self.project_id}.{self.dataset}.{checkpoint_table}"
        self.client = bigquery.Client(project=self.project_id, location=config["gcp"].get("location"))

    def get_checkpoint(self, table_name: str, schema_name: str | None = None) -> dict | None:
        """Latest checkpoint row for a table. Pass schema_name so that
        dbo.orders and sales.orders never read each other's checkpoint."""
        schema_filter = "AND schema_name = @schema_name" if schema_name else ""
        query = f"""
            SELECT last_pk_json, last_batch_index, status, run_id, updated_at
            FROM `{self.table_ref}`
            WHERE table_name = @table_name
            {schema_filter}
            ORDER BY updated_at DESC
            LIMIT 1
        """
        params = [bigquery.ScalarQueryParameter("table_name", "STRING", table_name)]
        if schema_name:
            params.append(bigquery.ScalarQueryParameter("schema_name", "STRING", schema_name))
        job_config = bigquery.QueryJobConfig(query_parameters=params)
        rows = list(self.client.query(query, job_config=job_config).result())
        if not rows:
            return None
        row = rows[0]
        return {
            "last_pk": json.loads(row.last_pk_json) if row.last_pk_json else None,
            "last_batch_index": row.last_batch_index,
            "status": row.status,
            "run_id": row.run_id,
        }

    def resume_point(self, table_name: str, schema_name: str | None = None,
                     checkpoint: dict | None = None) -> tuple[dict | None, int, bool]:
        """Returns (resume_after_pk, next_batch_index, is_resumed).
        (None, 0, False) means start this table from scratch — either no
        checkpoint exists yet, or the last run finished COMPLETED (in which
        case table_pipeline has already decided whether to skip the table
        or deliberately re-run it from the top).

        is_resumed is True whenever the last run left this table FAILED
        or IN_PROGRESS (i.e. it never reached COMPLETED).

        Pass `checkpoint` (the dict from get_checkpoint) to avoid a second
        BigQuery query when the caller already fetched it."""
        if checkpoint is None:
            checkpoint = self.get_checkpoint(table_name, schema_name)
        if not checkpoint or checkpoint["status"] == "COMPLETED":
            return None, 0, False
        # FAILED or IN_PROGRESS -> pick up right after the last committed batch.
        last_index = checkpoint["last_batch_index"]
        next_index = (-1 if last_index is None else last_index) + 1
        return checkpoint["last_pk"], next_index, True

    def commit_batch(self, run_id: str, table_cfg: dict, batch_index: int, last_pk: dict) -> None:
        self._write_row(run_id, table_cfg, batch_index, last_pk, status="IN_PROGRESS")

    def mark_completed(self, run_id: str, table_cfg: dict, batch_index: int, last_pk: dict | None) -> None:
        self._write_row(run_id, table_cfg, batch_index, last_pk, status="COMPLETED")

    def mark_failed(self, run_id: str, table_cfg: dict, batch_index: int, last_pk: dict | None) -> None:
        self._write_row(run_id, table_cfg, batch_index, last_pk, status="FAILED")

    def _write_row(self, run_id: str, table_cfg: dict, batch_index: int, last_pk: dict | None, status: str) -> None:
        row = {
            "table_name": table_cfg["name"],
            "schema_name": table_cfg["schema"],
            "last_pk_json": (
                json.dumps(
                last_pk,
                default=lambda o: o.item() if hasattr(o, "item") else str(o)
                )
                if last_pk is not None
                else None
            ),
            "last_batch_index": batch_index,
            "status": status,
            "run_id": run_id,
            "updated_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        }
        errors = self.client.insert_rows_json(self.table_ref, [row])
        if errors:
            raise RuntimeError(f"Failed to write checkpoint for {table_cfg['name']}: {errors}")