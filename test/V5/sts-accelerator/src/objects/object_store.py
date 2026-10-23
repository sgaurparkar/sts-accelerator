"""
object_store.py — the BigQuery side of the raw object migration.

Two tables in the control dataset of the target (<project>.migration_control_<location>):

  migration_sql_objects         one row per object (latest state), keyed by
                                resource_group / source_server / source_database /
                                object_type / object_schema / object_name
                                * definition            the ORIGINAL T-SQL, byte for byte
                                * definition_sha256     change detection between runs
                                * parameters/columns/references as JSON
                                * dependency_level      0 = depends only on tables; n = after level n-1
                                * source_status         PRESENT | DROPPED_IN_SOURCE
                                * change_status         NEW | CHANGED | UNCHANGED | DROPPED (last run)
                                * translation_status    PENDING (ready for the later translation
                                                        phase) | NOT_AVAILABLE (no definition)
                                  translated_sql / translated_at / bq_object / translation_notes
                                  are left empty for that phase. When the source definition
                                  changes they are cleared and the status goes back to PENDING.
  migration_sql_object_history  append-only: one row per NEW / CHANGED / DROPPED object per run
                                (every version of every definition is kept)

Writes use load jobs + one MERGE (never streaming inserts, so rows can be
updated immediately). A run is: load staging -> MERGE -> append history -> drop staging.
"""
import datetime
import time

from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from src.bigquery.bq_control_tables import SOURCE_KEY_FIELDS, BqControlTables, source_key_values
from src.bigquery.dataset_naming import control_table_ref, data_dataset
from src.common import log

LOG = log.get("object_store")

OBJECT_FIELDS = [
    ("object_schema", "STRING"), ("object_name", "STRING"), ("object_type", "STRING"),
    ("sql_type_code", "STRING"), ("sql_type_desc", "STRING"), ("parent_object", "STRING"),
    ("definition", "STRING"), ("definition_sha256", "STRING"), ("definition_length", "INT64"),
    ("definition_status", "STRING"),
    ("uses_ansi_nulls", "BOOL"), ("uses_quoted_identifier", "BOOL"), ("is_schema_bound", "BOOL"),
    ("parameters_json", "STRING"), ("returns_json", "STRING"), ("columns_json", "STRING"),
    ("references_json", "STRING"), ("references_cross_database", "BOOL"), ("dependency_level", "INT64"),
    ("source_created_at", "TIMESTAMP"), ("source_modified_at", "TIMESTAMP"),
    ("gcs_uri", "STRING"), ("change_status", "STRING"), ("translation_status", "STRING"),
    ("extracted_at", "TIMESTAMP"), ("run_id", "STRING"),
]
STAGING_SCHEMA = [(n, t, "NULLABLE") for n, t in SOURCE_KEY_FIELDS] + [(n, t, "NULLABLE") for n, t in OBJECT_FIELDS]

OBJECTS_SCHEMA = (
    [(n, t, "NULLABLE") for n, t in SOURCE_KEY_FIELDS]
    + [(n, t, "NULLABLE") for n, t in OBJECT_FIELDS if n not in ("extracted_at",)]
    + [("source_status", "STRING", "NULLABLE"),
       ("first_seen_at", "TIMESTAMP", "NULLABLE"), ("last_extracted_at", "TIMESTAMP", "NULLABLE"),
       ("last_changed_at", "TIMESTAMP", "NULLABLE"),
       ("translated_sql", "STRING", "NULLABLE"), ("translated_at", "TIMESTAMP", "NULLABLE"),
       ("bq_object", "STRING", "NULLABLE"), ("translation_notes", "STRING", "NULLABLE")]
)
HISTORY_SCHEMA = [(n, t, "NULLABLE") for n, t in SOURCE_KEY_FIELDS] + [
    ("object_schema", "STRING", "NULLABLE"), ("object_name", "STRING", "NULLABLE"),
    ("object_type", "STRING", "NULLABLE"), ("change_status", "STRING", "NULLABLE"),
    ("definition", "STRING", "NULLABLE"), ("definition_sha256", "STRING", "NULLABLE"),
    ("definition_status", "STRING", "NULLABLE"), ("source_modified_at", "TIMESTAMP", "NULLABLE"),
    ("gcs_uri", "STRING", "NULLABLE"), ("extracted_at", "TIMESTAMP", "NULLABLE"), ("run_id", "STRING", "NULLABLE"),
]

_KEY = ("resource_group", "source_server", "source_database", "object_type")
_UNCHANGED_SET = ["gcp_dataset", "sql_type_code", "sql_type_desc", "parent_object", "definition_status",
                  "uses_ansi_nulls", "uses_quoted_identifier", "is_schema_bound", "parameters_json", "returns_json",
                  "columns_json", "references_json", "references_cross_database", "dependency_level",
                  "source_created_at", "source_modified_at"]
_CHANGED_SET = _UNCHANGED_SET + ["object_schema", "object_name", "definition", "definition_sha256",
                                 "definition_length", "translation_status"]


def _fields(pairs):
    return [bigquery.SchemaField(n, t, mode=m) for n, t, m in pairs]


def build_objects_merge_sql(target_ref: str, staging_ref: str, mark_dropped: bool) -> str:
    on = " AND ".join(f"T.{k} = S.{k}" for k in _KEY)
    on += " AND LOWER(T.object_schema) = LOWER(S.object_schema) AND LOWER(T.object_name) = LOWER(S.object_name)"
    common_tail = ("T.source_status = 'PRESENT', T.last_extracted_at = S.extracted_at, T.run_id = S.run_id, "
                   "T.gcs_uri = COALESCE(S.gcs_uri, T.gcs_uri)")
    unchanged = ", ".join(f"T.{c} = S.{c}" for c in _UNCHANGED_SET)
    changed = ", ".join(f"T.{c} = S.{c}" for c in _CHANGED_SET)
    insert_cols = [n for n, _, _ in OBJECTS_SCHEMA]
    values = {
        "source_status": "'PRESENT'", "first_seen_at": "S.extracted_at", "last_extracted_at": "S.extracted_at",
        "last_changed_at": "S.extracted_at", "change_status": "'NEW'", "translated_sql": "NULL",
        "translated_at": "NULL", "bq_object": "NULL", "translation_notes": "NULL",
    }
    insert_vals = [values.get(c, f"S.{c}") for c in insert_cols]
    sql = (f"MERGE `{target_ref}` T\nUSING `{staging_ref}` S\nON {on}\n"
           f"WHEN MATCHED AND T.definition_sha256 IS NOT DISTINCT FROM S.definition_sha256 THEN UPDATE SET\n"
           f"  {unchanged}, {common_tail},\n  T.change_status = 'UNCHANGED'\n"
           f"WHEN MATCHED THEN UPDATE SET\n"
           f"  {changed}, {common_tail},\n  T.change_status = 'CHANGED', T.last_changed_at = S.extracted_at,\n"
           f"  T.translated_sql = NULL, T.translated_at = NULL, T.bq_object = NULL, T.translation_notes = NULL\n"
           f"WHEN NOT MATCHED THEN INSERT ({', '.join(insert_cols)})\n  VALUES ({', '.join(insert_vals)})")
    if mark_dropped:
        sql += ("\nWHEN NOT MATCHED BY SOURCE AND T.resource_group = @rg AND T.source_server = @srv "
                "AND T.source_database = @db AND T.object_type IN UNNEST(@kinds) AND T.source_status = 'PRESENT' "
                "THEN UPDATE SET T.source_status = 'DROPPED_IN_SOURCE', T.change_status = 'DROPPED', "
                "T.last_extracted_at = @now, T.last_changed_at = @now, T.run_id = @run")
    return sql


class ObjectStore:
    def __init__(self, settings: dict, client=None, sleep=time.sleep):
        self.settings = settings
        self._sleep = sleep
        md = settings.get("metadata") or {}
        self.objects_table = md.get("objects_table") or "migration_sql_objects"
        self.history_table = md.get("objects_history_table") or "migration_sql_object_history"
        self.objects_ref = control_table_ref(settings, self.objects_table)
        self.history_ref = control_table_ref(settings, self.history_table)
        self.staging_ref = control_table_ref(settings, f"stg__objects__{data_dataset(settings)}"[:1024])
        self.keys = source_key_values(settings)
        self._client = client

    @property
    def client(self):
        if self._client is None:
            self._client = bigquery.Client(project=self.settings["gcp"]["project_id"],
                                           location=self.settings["gcp"].get("location"))
        return self._client

    def ensure_tables(self, control: BqControlTables | None = None) -> None:
        control = control or BqControlTables(self.settings, client=self._client)
        control._ensure_control_table(self.objects_table, OBJECTS_SCHEMA, None)
        control._ensure_control_table(self.history_table, HISTORY_SCHEMA, "extracted_at")

    def existing(self) -> dict[tuple, dict]:
        """(object_type, schema.lower(), name.lower()) -> {sha256, source_status, gcs_uri} for this database."""
        sql = (f"SELECT object_type, object_schema, object_name, definition_sha256, source_status, gcs_uri "
               f"FROM `{self.objects_ref}` WHERE resource_group = @rg AND source_server = @srv "
               f"AND source_database = @db")
        cfg = bigquery.QueryJobConfig(query_parameters=self._key_params())
        try:
            rows = self.client.query(sql, job_config=cfg).result()
        except NotFound:
            return {}
        return {(r.object_type, str(r.object_schema).lower(), str(r.object_name).lower()):
                {"sha256": r.definition_sha256, "source_status": r.source_status, "gcs_uri": r.gcs_uri,
                 "schema": r.object_schema, "name": r.object_name}
                for r in rows}

    def _key_params(self):
        return [bigquery.ScalarQueryParameter("rg", "STRING", self.keys["resource_group"]),
                bigquery.ScalarQueryParameter("srv", "STRING", self.keys["source_server"]),
                bigquery.ScalarQueryParameter("db", "STRING", self.keys["source_database"])]

    def _dml_with_retry(self, sql: str, params: list, attempts: int = 5):
        """Several databases of one project merge into the same table; BigQuery may
        reject a concurrent DML ("Could not serialize access ...") — retry with backoff."""
        for attempt in range(1, attempts + 1):
            try:
                job = self.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params))
                job.result()
                return job
            except Exception as e:
                text = str(e).lower()
                if attempt == attempts or not ("serialize" in text or "concurrent" in text):
                    raise
                wait = 5 * attempt
                LOG.warning("MERGE into %s hit a concurrent update, retry %d/%d in %ss", self.objects_ref,
                            attempt, attempts - 1, wait)
                self._sleep(wait)
        raise AssertionError("unreachable")

    def write(self, rows: list[dict], history_rows: list[dict], kinds: list[str], mark_dropped: bool,
              run_id: str) -> int:
        """Stages `rows`, merges them into migration_sql_objects (optionally marking
        objects of `kinds` that are gone from the source), appends `history_rows`.
        Returns the MERGE's affected-row count."""
        affected = 0
        if rows or mark_dropped:
            load_cfg = bigquery.LoadJobConfig(schema=_fields(STAGING_SCHEMA),
                                              write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                                              source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
            if rows:
                self.client.load_table_from_json(rows, self.staging_ref, job_config=load_cfg).result()
            else:       # nothing left in the source: an empty staging table marks everything dropped
                self.client.delete_table(self.staging_ref, not_found_ok=True)
                self.client.create_table(bigquery.Table(self.staging_ref, schema=_fields(STAGING_SCHEMA)))
            try:
                params = []
                if mark_dropped:
                    now = datetime.datetime.now(datetime.timezone.utc)
                    params = self._key_params() + [
                        bigquery.ArrayQueryParameter("kinds", "STRING", list(kinds)),
                        bigquery.ScalarQueryParameter("now", "TIMESTAMP", now),
                        bigquery.ScalarQueryParameter("run", "STRING", run_id)]
                sql = build_objects_merge_sql(self.objects_ref, self.staging_ref, mark_dropped)
                job = self._dml_with_retry(sql, params)
                affected = int(job.num_dml_affected_rows or 0)
            finally:
                self.client.delete_table(self.staging_ref, not_found_ok=True)
        if history_rows:
            hist_cfg = bigquery.LoadJobConfig(schema=_fields(HISTORY_SCHEMA),
                                              write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
                                              source_format=bigquery.SourceFormat.NEWLINE_DELIMITED_JSON)
            self.client.load_table_from_json(history_rows, self.history_ref, job_config=hist_cfg).result()
        return affected
