"""
report_generator.py

Builds the human-readable "how is the migration doing, table by
table" report — as opposed to migration_pipeline_logs (raw, one row
per stage event) and migration_audit (one row per table PER RUN).
This module rolls those up into one row per table showing its latest
run's outcome plus its actual current row count in BigQuery, and:

  1. Writes it as a real BigQuery table (CREATE OR REPLACE — a fresh
     full snapshot every time this runs, not an append), so it can be
     queried with SQL like any other table.
  2. Also writes a local JSON snapshot under logs/reports/, dated, so
     there's an offline copy without needing BigQuery access to read it.

migration_audit lives in the base dataset (settings.gcp.bq_dataset) —
it's cross-schema, one row per table per run. A table's actual DATA,
though, lives in a dataset scoped to its own source schema (see
src/bigquery/dataset_naming.py), so the live row count is looked up
there, using each audit row's schema_name.

Raw per-event logs (JSON) already live under logs/raw/ locally, or
gcs://<bucket>/pipeline_logs/raw/ in production — see
src/metadata/metadata_manager.py. This module is what turns those
(via migration_audit) into the tablewise summary.
"""
import datetime
import json
import os

from google.cloud import bigquery

from src.bigquery.dataset_naming import dataset_for_schema


class ReportGenerator:
    def __init__(self, settings: dict, logging_config: dict):
        self.project_id = settings["gcp"]["project_id"]
        self.base_dataset = settings["gcp"]["bq_dataset"]
        metadata_cfg = settings.get("metadata", {})
        self.audit_table = metadata_cfg.get("audit_table", "migration_audit")
        self.report_table = metadata_cfg.get("report_table", "migration_report")
        self.report_output_path = logging_config.get("report_output_path", "logs/reports")
        self.client = bigquery.Client(project=self.project_id)

    def _latest_audit_per_table(self) -> list[dict]:
        """One row per table_name: its most recent audit entry."""
        query = f"""
            SELECT table_name, schema_name, load_mode, outcome,
                   batches_processed, rows_processed, started_at,
                   finished_at, error_message
            FROM (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY table_name ORDER BY finished_at DESC
                       ) AS rn
                FROM `{self.project_id}.{self.base_dataset}.{self.audit_table}`
            )
            WHERE rn = 1
        """
        return [dict(row) for row in self.client.query(query).result()]

    def _live_row_count(self, table_name: str, schema_name: str) -> int | None:
        """Actual current row count in the migrated target table right now
        (not the count from any one run — the true total after every
        insert-only merge to date). Looked up in the dataset scoped to
        the table's own source schema, not the base dataset."""
        dataset = dataset_for_schema(self.base_dataset, schema_name) if schema_name else self.base_dataset
        try:
            query = f"SELECT COUNT(*) AS cnt FROM `{self.project_id}.{dataset}.{table_name}`"
            rows = list(self.client.query(query).result())
            return rows[0].cnt
        except Exception:
            # Target table may not exist yet if the table's first run failed
            # before ever creating it — that's a legitimate report state,
            # not a crash.
            return None

    def build_rows(self) -> list[dict]:
        print("[report_generator] Fetching latest audit entry per table ...")
        audits = self._latest_audit_per_table()
        print(f"[report_generator] {len(audits)} table(s) have an audit entry")

        rows = []
        for audit in audits:
            table_name = audit["table_name"]
            schema_name = audit["schema_name"]
            dataset = dataset_for_schema(self.base_dataset, schema_name) if schema_name else self.base_dataset
            print(f"[report_generator] Counting live rows for {schema_name}.{table_name} "
                  f"in {dataset} ...")
            live_count = self._live_row_count(table_name, schema_name)
            rows.append({
                "table_name": table_name,
                "schema_name": schema_name,
                "bq_dataset": dataset,
                "load_mode": audit["load_mode"],
                "last_run_outcome": audit["outcome"],
                "last_run_rows_processed": audit["rows_processed"],
                "last_run_started_at": _iso(audit["started_at"]),
                "last_run_finished_at": _iso(audit["finished_at"]),
                "last_error_message": audit["error_message"],
                "total_rows_in_bigquery": live_count,
                "report_generated_at": datetime.datetime.utcnow().isoformat(),
            })
        return rows

    def write_bigquery_report(self, rows: list[dict]) -> str:
        table_ref = f"{self.project_id}.{self.base_dataset}.{self.report_table}"
        schema = [
            bigquery.SchemaField("table_name", "STRING"),
            bigquery.SchemaField("schema_name", "STRING"),
            bigquery.SchemaField("bq_dataset", "STRING"),
            bigquery.SchemaField("load_mode", "STRING"),
            bigquery.SchemaField("last_run_outcome", "STRING"),
            bigquery.SchemaField("last_run_rows_processed", "INT64"),
            bigquery.SchemaField("last_run_started_at", "TIMESTAMP"),
            bigquery.SchemaField("last_run_finished_at", "TIMESTAMP"),
            bigquery.SchemaField("last_error_message", "STRING"),
            bigquery.SchemaField("total_rows_in_bigquery", "INT64"),
            bigquery.SchemaField("report_generated_at", "TIMESTAMP"),
        ]
        # Full snapshot every time — delete-and-recreate rather than append,
        # since this represents "current status", not a history log.
        print(f"[report_generator] Writing report table {table_ref} "
              f"({len(rows)} row(s)) ...")
        self.client.delete_table(table_ref, not_found_ok=True)
        table = bigquery.Table(table_ref, schema=schema)
        self.client.create_table(table)
        if rows:
            errors = self.client.insert_rows_json(table_ref, rows)
            if errors:
                print(f"[report_generator] WARNING: report insert failed: {errors}")
        print(f"[report_generator] Report table ready: {table_ref}")
        return table_ref

    def write_local_snapshot(self, rows: list[dict]) -> str:
        os.makedirs(self.report_output_path, exist_ok=True)
        filename = f"migration_report_{datetime.date.today().isoformat()}.json"
        path = os.path.join(self.report_output_path, filename)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, default=str)
        print(f"[report_generator] Local snapshot written: {path}")
        return path

    def generate(self) -> dict:
        print("[report_generator] Generating tablewise migration report ...")
        rows = self.build_rows()
        bq_table = self.write_bigquery_report(rows)
        local_path = self.write_local_snapshot(rows)
        return {"tables_reported": len(rows), "bigquery_table": bq_table, "local_snapshot": local_path}


def _iso(value) -> str | None:
    return value.isoformat() if value is not None else None
