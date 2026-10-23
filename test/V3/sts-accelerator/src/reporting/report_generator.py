"""
report_generator.py — the table-by-table status report for one control dataset
(= one GCP project + location).

One row per (database, schema, table, scope) with its latest outcome from
migration_audit, the last reconciliation result and the live row count of the
BigQuery table. Row counts come from one metadata query per dataset
(`<dataset>.__TABLES__`, free), not one COUNT(*) per table.

Written to <project>.<control dataset>.migration_report (replaced each time)
and as JSON under logs/reports/<project>/<control dataset>/.
"""
import datetime
import json
import os

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from src.bigquery.dataset_naming import control_table_ref
from src.common import log

LOG = log.get("report")
_KEYS = ("resource_group", "source_server", "source_database", "gcp_dataset")

REPORT_SCHEMA = (
    ("resource_group", "STRING"), ("source_server", "STRING"), ("source_database", "STRING"),
    ("gcp_project", "STRING"), ("gcp_dataset", "STRING"), ("bq_table", "STRING"),
    ("table_name", "STRING"), ("schema_name", "STRING"), ("load_mode", "STRING"), ("scope_key", "STRING"),
    ("last_run_outcome", "STRING"), ("last_run_rows_processed", "INT64"),
    ("last_source_rows", "INT64"), ("last_target_rows", "INT64"), ("last_reconciled", "BOOL"),
    ("last_run_started_at", "TIMESTAMP"), ("last_run_finished_at", "TIMESTAMP"),
    ("last_error_message", "STRING"), ("total_rows_in_bigquery", "INT64"), ("report_generated_at", "TIMESTAMP"),
)


def _iso(value):
    return value.isoformat() if value is not None else None


class ReportGenerator:
    def __init__(self, settings: dict, logging_config: dict, client=None):
        self.settings = settings
        self.project_id = settings["gcp"]["project_id"]
        self.control_dataset = settings["gcp"]["control_dataset"]
        md = settings.get("metadata") or {}
        self.audit_ref = control_table_ref(settings, md.get("audit_table", "migration_audit"))
        self.report_ref = control_table_ref(settings, md.get("report_table", "migration_report"))
        self.out_dir = os.path.join(logging_config.get("report_output_path", "logs/reports"),
                                    self.project_id, self.control_dataset)
        self.client = client or bigquery.Client(project=self.project_id, location=settings["gcp"].get("location"))

    def _latest_audits(self) -> list[dict]:
        query = f"""
            SELECT * EXCEPT(rn) FROM (
              SELECT *, ROW_NUMBER() OVER (
                PARTITION BY resource_group, source_server, source_database, schema_name, table_name,
                             IFNULL(scope_key, load_mode)
                ORDER BY finished_at DESC) AS rn
              FROM `{self.audit_ref}`)
            WHERE rn = 1
            ORDER BY resource_group, source_server, source_database, schema_name, table_name
        """
        try:
            return [dict(r) for r in self.client.query(query).result()]
        except NotFound:
            LOG.info("%s does not exist yet — nothing has run here", self.audit_ref)
            return []

    def _row_counts(self, dataset: str) -> dict[str, int]:
        try:
            rows = self.client.query(f"SELECT table_id, row_count FROM `{self.project_id}.{dataset}.__TABLES__`").result()
            return {r.table_id.lower(): int(r.row_count or 0) for r in rows}
        except Exception as e:
            LOG.warning("Could not read row counts of %s: %s", dataset, e)
            return {}

    def build_rows(self) -> list[dict]:
        audits = self._latest_audits()
        counts: dict[str, dict] = {}
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        rows = []
        for a in audits:
            ds = a.get("gcp_dataset")
            if ds and ds not in counts:
                counts[ds] = self._row_counts(ds)
            rows.append({
                **{k: a.get(k) for k in _KEYS},
                "gcp_project": self.project_id, "bq_table": a.get("bq_table"),
                "table_name": a.get("table_name"), "schema_name": a.get("schema_name"),
                "load_mode": a.get("load_mode"), "scope_key": a.get("scope_key"),
                "last_run_outcome": a.get("outcome"), "last_run_rows_processed": a.get("rows_processed"),
                "last_source_rows": a.get("source_rows"), "last_target_rows": a.get("target_rows"),
                "last_reconciled": a.get("reconciled"),
                "last_run_started_at": _iso(a.get("started_at")), "last_run_finished_at": _iso(a.get("finished_at")),
                "last_error_message": a.get("error_message"),
                "total_rows_in_bigquery": (counts.get(ds) or {}).get(str(a.get("bq_table") or "").lower()),
                "report_generated_at": now,
            })
        return rows

    def write_bigquery_report(self, rows: list[dict]) -> str:
        schema = [bigquery.SchemaField(n, t) for n, t in REPORT_SCHEMA]
        if rows:
            cfg = bigquery.LoadJobConfig(schema=schema, write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE)
            self.client.load_table_from_json(rows, self.report_ref, job_config=cfg).result()
        else:
            self.client.delete_table(self.report_ref, not_found_ok=True)
            self.client.create_table(bigquery.Table(self.report_ref, schema=schema))
        return self.report_ref

    def write_local_snapshot(self, rows: list[dict]) -> str:
        os.makedirs(self.out_dir, exist_ok=True)
        path = os.path.join(self.out_dir, f"migration_report_{datetime.date.today().isoformat()}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(rows, f, indent=2, default=str)
        return path

    def generate(self) -> dict:
        rows = self.build_rows()
        result = {"tables_reported": len(rows), "bigquery_table": self.write_bigquery_report(rows),
                  "local_snapshot": self.write_local_snapshot(rows)}
        LOG.info("Report %s: %d table(s) (local copy %s)", result["bigquery_table"], len(rows), result["local_snapshot"])
        return result
