"""
bq_control_tables.py

Ensures every BigQuery object the pipeline needs exists, all built
dynamically from what's discovered/planned at runtime — nothing here
is a hand-maintained DDL script:

  - one BigQuery dataset PER SOURCE SCHEMA for the actual migrated
    data — a table discovered in SQL Server schema `dbo` lands in
    dataset `<gcp.bq_dataset>_dbo`, one discovered in `sales` lands in
    `<gcp.bq_dataset>_sales`, and so on (see dataset_naming.py). This
    keeps the source schema structure visible and separated in
    BigQuery instead of flattening every schema into one dataset.
  - each table's TARGET table       (schema from type_mapper — plain,
                                      unpartitioned, unclustered),
                                      created inside that schema's
                                      dataset
  - each table's STAGING table      (mirrors the target, holds one batch
                                      at a time before MERGE), created
                                      alongside the target table
  - migration_pipeline_logs         (raw stage-event log, as a queryable
                                      table — see src/metadata/metadata_manager.py)
  - migration_checkpoint            (last committed primary key per table,
                                      used to resume failed/partial runs)
  - migration_audit                 (one row per table per run: final
                                      outcome, rows processed, timing)

The four operational tables above are NOT schema-scoped — they stay
in the single base dataset (gcp.bq_dataset) and carry a schema_name
column instead, since they describe the pipeline's own run history
across every schema at once, not source data.

All CREATE TABLE IF NOT EXISTS / dataset creation is idempotent, so
calling these repeatedly is safe.
"""
from google.cloud import bigquery

from src.planner.type_mapper import build_bigquery_schema
from src.bigquery.dataset_naming import dataset_for_schema


class BqControlTables:
    def __init__(self, config: dict):
        self.project_id = config["gcp"]["project_id"]
        # Base dataset — home of the operational/control tables
        # (logs, checkpoint, audit). Per-schema data datasets are
        # derived from this via dataset_for_schema().
        self.dataset = config["gcp"]["bq_dataset"]
        self.client = bigquery.Client(project=self.project_id)
        self._ensured_datasets: set[str] = set()

    def _table_ref(self, table_name: str, dataset: str | None = None) -> str:
        return f"{self.project_id}.{dataset or self.dataset}.{table_name}"

    def dataset_for_table(self, table_cfg: dict) -> str:
        """The dataset a table's target/staging tables live in, derived
        from its source schema (table_cfg['schema'])."""
        return dataset_for_schema(self.dataset, table_cfg["schema"])

    def ensure_dataset(self, dataset_id: str | None = None) -> None:
        dataset_id = dataset_id or self.dataset
        if dataset_id in self._ensured_datasets:
            return
        dataset_ref = bigquery.DatasetReference(self.project_id, dataset_id)
        try:
            self.client.get_dataset(dataset_ref)
            print(f"[bq_control_tables] Dataset already exists: {self.project_id}.{dataset_id}")
        except Exception:
            print(f"[bq_control_tables] Creating dataset {self.project_id}.{dataset_id} ...")
            self.client.create_dataset(bigquery.Dataset(dataset_ref), exists_ok=True)
            print(f"[bq_control_tables] Dataset ready: {self.project_id}.{dataset_id}")
        self._ensured_datasets.add(dataset_id)

    def _ensure_data_table(self, table_name: str, table_cfg: dict, dataset: str) -> None:
        table_ref = self._table_ref(table_name, dataset)
        try:
            self.client.get_table(table_ref)
            print(f"[bq_control_tables] Table already exists: {table_ref}")
            return
        except Exception:
            pass

        print(f"[bq_control_tables] Creating table {table_ref} "
              f"({len(table_cfg['columns'])} columns, from schema '{table_cfg['schema']}') ...")
        schema = [
            bigquery.SchemaField(col["name"], col["type"])
            for col in build_bigquery_schema(table_cfg["columns"])
        ]
        # Plain table: no time partitioning, no clustering.
        table = bigquery.Table(table_ref, schema=schema)
        self.client.create_table(table, exists_ok=True)
        print(f"[bq_control_tables] Table ready: {table_ref}")

    def ensure_target_table(self, table_cfg: dict) -> str:
        dataset = self.dataset_for_table(table_cfg)
        self.ensure_dataset(dataset)
        self._ensure_data_table(table_cfg["name"], table_cfg, dataset)
        return self._table_ref(table_cfg["name"], dataset)

    def ensure_staging_table(self, table_cfg: dict) -> str:
        dataset = self.dataset_for_table(table_cfg)
        self.ensure_dataset(dataset)
        staging_name = f"{table_cfg['name']}_staging"
        self._ensure_data_table(staging_name, table_cfg, dataset)
        return self._table_ref(staging_name, dataset)

    def ensure_log_table(self, log_table_name: str) -> str:
        self.ensure_dataset()
        table_ref = self._table_ref(log_table_name)
        try:
            self.client.get_table(table_ref)
            return table_ref
        except Exception:
            pass
        print(f"[bq_control_tables] Creating control table {table_ref} ...")
        schema = [
            bigquery.SchemaField("run_id", "STRING"),
            bigquery.SchemaField("table_name", "STRING"),
            bigquery.SchemaField("schema_name", "STRING"),
            bigquery.SchemaField("stage", "STRING"),
            bigquery.SchemaField("status", "STRING"),
            bigquery.SchemaField("batch_index", "INT64"),
            bigquery.SchemaField("rows_processed", "INT64"),
            bigquery.SchemaField("bytes_processed", "INT64"),
            bigquery.SchemaField("started_at", "TIMESTAMP"),
            bigquery.SchemaField("finished_at", "TIMESTAMP"),
            bigquery.SchemaField("error_message", "STRING"),
            bigquery.SchemaField("extra_json", "STRING"),
        ]
        table = bigquery.Table(table_ref, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field="started_at"
        )
        self.client.create_table(table, exists_ok=True)
        print(f"[bq_control_tables] Control table ready: {table_ref}")
        return table_ref

    def ensure_checkpoint_table(self, checkpoint_table_name: str) -> str:
        self.ensure_dataset()
        table_ref = self._table_ref(checkpoint_table_name)
        try:
            self.client.get_table(table_ref)
            return table_ref
        except Exception:
            pass
        print(f"[bq_control_tables] Creating control table {table_ref} ...")
        schema = [
            bigquery.SchemaField("table_name", "STRING", mode="REQUIRED"),
            bigquery.SchemaField("schema_name", "STRING"),
            bigquery.SchemaField("last_pk_json", "STRING"),
            bigquery.SchemaField("last_batch_index", "INT64"),
            bigquery.SchemaField("status", "STRING"),  # IN_PROGRESS | COMPLETED | FAILED
            bigquery.SchemaField("run_id", "STRING"),
            bigquery.SchemaField("updated_at", "TIMESTAMP"),
        ]
        table = bigquery.Table(table_ref, schema=schema)
        self.client.create_table(table, exists_ok=True)
        print(f"[bq_control_tables] Control table ready: {table_ref}")
        return table_ref

    def ensure_audit_table(self, audit_table_name: str) -> str:
        self.ensure_dataset()
        table_ref = self._table_ref(audit_table_name)
        try:
            self.client.get_table(table_ref)
            return table_ref
        except Exception:
            pass
        print(f"[bq_control_tables] Creating control table {table_ref} ...")
        schema = [
            bigquery.SchemaField("run_id", "STRING"),
            bigquery.SchemaField("table_name", "STRING"),
            bigquery.SchemaField("schema_name", "STRING"),
            bigquery.SchemaField("load_mode", "STRING"),
            bigquery.SchemaField("outcome", "STRING"),  # SUCCESS | FAILED | RESUMED_SUCCESS
            bigquery.SchemaField("batches_processed", "INT64"),
            bigquery.SchemaField("rows_processed", "INT64"),
            bigquery.SchemaField("resumed_from_batch", "INT64"),
            bigquery.SchemaField("started_at", "TIMESTAMP"),
            bigquery.SchemaField("finished_at", "TIMESTAMP"),
            bigquery.SchemaField("error_message", "STRING"),
        ]
        table = bigquery.Table(table_ref, schema=schema)
        table.time_partitioning = bigquery.TimePartitioning(
            type_=bigquery.TimePartitioningType.DAY, field="started_at"
        )
        self.client.create_table(table, exists_ok=True)
        print(f"[bq_control_tables] Control table ready: {table_ref}")
        return table_ref
