"""
bq_control_tables.py — datasets and operational tables for ONE migration target.

  data dataset     <project>.<server>_<database>     only migrated tables; labelled with
                                                     the Azure RG / server / database / location
  control dataset  <project>.migration_control_<loc> checkpoint, audit, logs, report, locks and
                                                     the per-table staging tables

Both are created in the target's GCP location; an existing dataset in another
location is rejected. Control rows carry resource_group / source_server /
source_database (lower-cased, so Azure returning a different case never splits
a table's history) and gcp_dataset. Older control tables are upgraded in place
(missing columns added).
"""
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from src.bigquery.dataset_naming import control_dataset, data_dataset
from src.common import log
from src.topology.naming import slug

LOG = log.get("bq_control")
_ENSURED: set[str] = set()      # datasets / tables already verified in this process

SOURCE_KEY_FIELDS = [
    ("resource_group", "STRING"),
    ("source_server", "STRING"),
    ("source_database", "STRING"),
    ("gcp_dataset", "STRING"),
]


def source_key_values(settings: dict) -> dict:
    src = settings.get("source") or {}

    def low(v):
        return str(v).lower() if v is not None else None
    return {
        "resource_group": low(src.get("resource_group")),
        "source_server": low(src.get("server")),
        "source_database": low(src.get("database")),
        "gcp_dataset": (settings.get("gcp") or {}).get("dataset"),
    }


def _with_keys(pairs):
    return [(n, t, "NULLABLE") for n, t in SOURCE_KEY_FIELDS] + pairs


LOG_SCHEMA = _with_keys([
    ("run_id", "STRING", "NULLABLE"), ("table_name", "STRING", "NULLABLE"),
    ("schema_name", "STRING", "NULLABLE"), ("scope_key", "STRING", "NULLABLE"),
    ("stage", "STRING", "NULLABLE"), ("status", "STRING", "NULLABLE"),
    ("batch_index", "INT64", "NULLABLE"), ("rows_processed", "INT64", "NULLABLE"),
    ("bytes_processed", "INT64", "NULLABLE"), ("started_at", "TIMESTAMP", "NULLABLE"),
    ("finished_at", "TIMESTAMP", "NULLABLE"), ("error_message", "STRING", "NULLABLE"),
    ("extra_json", "STRING", "NULLABLE"),
])
CHECKPOINT_SCHEMA = _with_keys([
    ("table_name", "STRING", "REQUIRED"), ("schema_name", "STRING", "NULLABLE"),
    ("scope_key", "STRING", "NULLABLE"),          # full | incremental | partial:<hash>
    ("load_mode", "STRING", "NULLABLE"),
    ("status", "STRING", "NULLABLE"),             # IN_PROGRESS | COMPLETED | FAILED
    ("last_pk_json", "STRING", "NULLABLE"),       # typed JSON of the key of the last uploaded file (resume point)
    ("last_batch_index", "INT64", "NULLABLE"),    # Parquet files already uploaded in run_folder
    ("rows_done", "INT64", "NULLABLE"),
    ("low_watermark_json", "STRING", "NULLABLE"),
    ("high_watermark_json", "STRING", "NULLABLE"),
    ("watermark_json", "STRING", "NULLABLE"),     # committed watermark (COMPLETED incremental)
    ("run_id", "STRING", "NULLABLE"), ("updated_at", "TIMESTAMP", "NULLABLE"),
    ("run_folder", "STRING", "NULLABLE"),         # parquet/.../<scope>/run_<UTC>_<id>/ of this attempt
    ("phase", "STRING", "NULLABLE"),              # EXTRACTING | LANDED (where a failed run stopped)
    ("run_kind", "STRING", "NULLABLE"),           # INITIAL (first load, replaces) | INCREMENTAL | FULL | PARTIAL
])
AUDIT_SCHEMA = _with_keys([
    ("run_id", "STRING", "NULLABLE"), ("table_name", "STRING", "NULLABLE"),
    ("schema_name", "STRING", "NULLABLE"), ("bq_table", "STRING", "NULLABLE"),
    ("load_mode", "STRING", "NULLABLE"), ("scope_key", "STRING", "NULLABLE"),
    ("outcome", "STRING", "NULLABLE"),  # SUCCESS | RESUMED_SUCCESS | FAILED | SUCCESS_GCS_ONLY | NO_CHANGES
    ("batches_processed", "INT64", "NULLABLE"), ("rows_processed", "INT64", "NULLABLE"),
    ("resumed_from_batch", "INT64", "NULLABLE"),
    ("source_rows", "INT64", "NULLABLE"), ("target_rows", "INT64", "NULLABLE"),
    ("reconciled", "BOOL", "NULLABLE"), ("watermark_json", "STRING", "NULLABLE"),
    ("started_at", "TIMESTAMP", "NULLABLE"), ("finished_at", "TIMESTAMP", "NULLABLE"),
    ("error_message", "STRING", "NULLABLE"),
])
LOCK_SCHEMA = [
    ("lock_key", "STRING", "REQUIRED"), ("owner", "STRING", "NULLABLE"),
    ("acquired_at", "TIMESTAMP", "NULLABLE"), ("expires_at", "TIMESTAMP", "NULLABLE"),
]


def _fields(pairs):
    return [bigquery.SchemaField(n, t, mode=m) for n, t, m in pairs]


class BqControlTables:
    def __init__(self, config: dict, client=None):
        self.settings = config
        self.project_id = config["gcp"]["project_id"]
        self.location = config["gcp"].get("location")
        self.dataset = data_dataset(config)
        self.control_dataset = control_dataset(config)
        self.client = client or bigquery.Client(project=self.project_id, location=self.location)
        self._ensured = _ENSURED

    def _ref(self, table_name: str, dataset: str) -> str:
        return f"{self.project_id}.{dataset}.{table_name}"

    def _labels(self, control: bool) -> dict:
        src = self.settings.get("source") or {}
        labels = {"migrated_from": "azure-sql", "managed_by": "sts-accelerator"}
        if control:
            labels["purpose"] = "migration-control"
        else:
            labels.update({
                "azure_resource_group": slug(src.get("resource_group"), "-")[:63],
                "azure_server": slug(src.get("server"), "-")[:63],
                "azure_database": slug(src.get("database"), "-")[:63],
                "azure_location": slug(src.get("azure_location"), "-")[:63],
            })
        return {k: v for k, v in labels.items() if v}

    def ensure_dataset(self, dataset_id: str | None = None) -> None:
        dataset_id = dataset_id or self.dataset
        cache_key = f"{self.project_id}.{dataset_id}"
        if cache_key in self._ensured:
            return
        control = dataset_id == self.control_dataset
        ref = bigquery.DatasetReference(self.project_id, dataset_id)
        try:
            existing = self.client.get_dataset(ref)
        except NotFound:
            src = self.settings.get("source") or {}
            LOG.info("Creating dataset %s.%s in %s", self.project_id, dataset_id, self.location)
            ds = bigquery.Dataset(ref)
            if self.location:
                ds.location = self.location
            ds.labels = self._labels(control)
            ds.description = ("Migration control tables (checkpoint, audit, logs, report, locks, staging)"
                              if control else
                              f"Migrated from Azure SQL: resource group {src.get('resource_group')}, server "
                              f"{src.get('server')}, database {src.get('database')} ({src.get('azure_location')})")
            self.client.create_dataset(ds, exists_ok=True)
        else:
            have = (existing.location or "").lower()
            if self.location and have and have != self.location.lower():
                raise RuntimeError(
                    f"Dataset {self.project_id}.{dataset_id} already exists in {have}, but this database maps to "
                    f"{self.location} — BigQuery can't load GCS data across locations. Rename it "
                    "(gcp.datasets.overrides) or change gcp.location_mapping.")
        self._ensured.add(cache_key)

    def _ensure_control_table(self, name: str, schema_pairs, partition_field: str | None) -> str:
        self.ensure_dataset(self.control_dataset)
        ref = self._ref(name, self.control_dataset)
        if ref in self._ensured:
            return ref
        wanted = _fields(schema_pairs)
        try:
            table = self.client.get_table(ref)
        except NotFound:
            table = bigquery.Table(ref, schema=wanted)
            if partition_field:
                table.time_partitioning = bigquery.TimePartitioning(
                    type_=bigquery.TimePartitioningType.DAY, field=partition_field)
            self.client.create_table(table, exists_ok=True)
            self._ensured.add(ref)
            return ref
        have = {f.name for f in table.schema}
        missing = [f for f in wanted if f.name not in have and f.mode != "REQUIRED"]
        if missing:
            LOG.info("Adding column(s) %s to %s", [f.name for f in missing], ref)
            table.schema = list(table.schema) + missing
            self.client.update_table(table, ["schema"])
        self._ensured.add(ref)
        return ref

    def _name(self, key: str, default: str) -> str:
        return (self.settings.get("metadata") or {}).get(key) or default

    def ensure_log_table(self, name: str | None = None) -> str:
        return self._ensure_control_table(name or self._name("log_table", "migration_pipeline_logs"),
                                          LOG_SCHEMA, "started_at")

    def ensure_checkpoint_table(self, name: str | None = None) -> str:
        return self._ensure_control_table(name or self._name("checkpoint_table", "migration_checkpoint"),
                                          CHECKPOINT_SCHEMA, None)

    def ensure_audit_table(self, name: str | None = None) -> str:
        return self._ensure_control_table(name or self._name("audit_table", "migration_audit"),
                                          AUDIT_SCHEMA, "started_at")

    def ensure_lock_table(self, name: str | None = None) -> str:
        return self._ensure_control_table(name or self._name("lock_table", "migration_lock"), LOCK_SCHEMA, None)

    def ensure_all(self) -> None:
        self.ensure_dataset(self.dataset)
        self.ensure_dataset(self.control_dataset)
        self.ensure_log_table()
        self.ensure_checkpoint_table()
        self.ensure_audit_table()
        self.ensure_lock_table()
