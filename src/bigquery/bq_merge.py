"""
bq_merge.py

INSERT-ONLY. This pipeline's source data is static — existing rows
never change, they only ever get new rows appended. So every table
lands in BigQuery through the same path: load one batch's Parquet
file into that table's staging table, then MERGE it into the target
matching on the table's real (possibly composite) primary key, with
NO "WHEN MATCHED" branch at all. Rows already present in the target
are left completely untouched; only rows not yet present (by primary
key) get inserted.

Target/staging live in a dataset scoped to the table's SOURCE SCHEMA
(dbo, sales, ...) — see src/bigquery/dataset_naming.py — not a single
flat dataset for every table regardless of where it came from.

This is still done as a MERGE rather than a plain INSERT because it's
what makes the failure-recovery story safe:

  If a batch fails partway through a table, the next run resumes
  extraction from the last checkpointed primary key (see
  src/metadata/checkpoint_manager.py) and re-runs this same
  insert-only MERGE forward from there. If a batch had actually
  landed before the failure was recorded, re-running it is a no-op
  (every row in it already matches on primary key, so nothing is
  inserted again) rather than creating duplicates — at-least-once
  batch delivery is always safe.

There is no watermark column anywhere in this design — it isn't
needed for insert-only behavior, since "already exists by primary
key" is the only check a pure-insert merge requires.
"""
from google.cloud import bigquery

from src.bigquery.dataset_naming import dataset_for_schema
from src.planner.type_mapper import build_bigquery_schema


class BqMerge:
    def __init__(self, config: dict):
        self.project_id = config["gcp"]["project_id"]
        self.base_dataset = config["gcp"]["bq_dataset"]
        self.gcs_bucket = config["gcp"]["gcs_bucket"]
        self.region_slug = config.get("source", {}).get("region_slug")
        self.client = bigquery.Client(project=self.project_id, location=config["gcp"].get("location"))

    def _dataset(self, table_cfg: dict) -> str:
        return dataset_for_schema(self.base_dataset, table_cfg["schema"], self.region_slug)

    def load_batch_to_staging(self, table_cfg: dict, batch_index: int) -> str:
        """Loads exactly this batch's Parquet file from GCS into the
        table's staging table (in its schema-scoped dataset),
        truncating staging first (it only ever holds one batch at a
        time)."""
        table_name = table_cfg["name"]
        schema_name = table_cfg["schema"]
        dataset = self._dataset(table_cfg)
        staging_ref = f"{self.project_id}.{dataset}.{table_name}_staging"
        uri = (
            f"gs://{self.gcs_bucket}/parquet/{schema_name}/{table_name}/"
            f"{table_name}_part{batch_index:04d}.parquet"
        )

        # Explicit schema is required here: for Parquet (a self-describing
        # format) combined with WRITE_TRUNCATE, BigQuery will otherwise
        # silently REDEFINE the staging table's schema from the Parquet
        # file's own physical types instead of preserving the NUMERIC /
        # TIMESTAMP / etc. types it was created with (see bq_control_tables.py).
        # Concretely: pandas reads SQL Server DECIMAL/NUMERIC columns as
        # float64, so the Parquet file stores them as physical DOUBLE — and
        # without a pinned schema, staging's column would flip from NUMERIC
        # to FLOAT64 on load, which then fails the MERGE into the target
        # (FLOAT64 cannot be inserted into a NUMERIC column). Passing the
        # same schema used to create the table forces BigQuery to convert
        # into it on load instead.
        schema = [
            bigquery.SchemaField(col["name"], col["type"])
            for col in build_bigquery_schema(table_cfg["columns"])
        ]

        print(f"[bq_merge] Loading batch {batch_index} for {schema_name}.{table_name} "
              f"into staging: {uri} -> {staging_ref}")
        job_config = bigquery.LoadJobConfig(
            source_format=bigquery.SourceFormat.PARQUET,
            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
            schema=schema,
        )
        load_job = self.client.load_table_from_uri(uri, staging_ref, job_config=job_config)
        load_job.result()
        print(f"[bq_merge] Staging load complete for {schema_name}.{table_name} "
              f"batch {batch_index}: {load_job.output_rows} row(s) staged")
        return staging_ref

    def merge_batch_into_target(self, table_cfg: dict) -> dict:
        table_name = table_cfg["name"]
        schema_name = table_cfg["schema"]
        pk_cols = table_cfg["primary_key"]

        if not pk_cols:
            raise ValueError(
                f"'{table_name}' has no primary key — cannot MERGE safely."
            )

        if table_cfg.get("synthetic_key"):
            # Guards against a table that was already fully COMPLETED once
            # before (which drops this column from the target — see
            # drop_synthetic_column below) being run through the pipeline
            # again, e.g. a synthetic-key table mistakenly left on
            # load_mode: incremental. Without this, the ON clause below
            # would reference a column that no longer exists on the target
            # and the MERGE would fail outright with "Unrecognized name".
            self._ensure_synthetic_column(table_cfg, pk_cols[0])

        dataset = self._dataset(table_cfg)
        columns = [c["name"] for c in table_cfg["columns"]]

        target_ref = f"`{self.project_id}.{dataset}.{table_name}`"
        staging_ref = f"`{self.project_id}.{dataset}.{table_name}_staging`"

        on_clause = " AND ".join(
            f"T.`{col}` = S.`{col}`"
            for col in pk_cols
        )

        insert_columns = ", ".join(
            f"`{col}`" for col in columns
        )

        insert_values = ", ".join(
            f"S.`{col}`" for col in columns
        )

        print(f"[bq_merge] Merging staging into target for {schema_name}.{table_name} "
              f"(dataset={dataset}, on={pk_cols}) ...")

        # No WHEN MATCHED branch at all — matched rows (already migrated,
        # by primary key) are left untouched. Only new rows get inserted.
        query = f"""
        MERGE {target_ref} T
        USING {staging_ref} S
        ON {on_clause}
        WHEN NOT MATCHED THEN
        INSERT ({insert_columns})
        VALUES ({insert_values})
        """
        job = self.client.query(query)
        job.result()

        print(f"[bq_merge] Merge complete for {schema_name}.{table_name}: "
              f"{job.num_dml_affected_rows} new row(s) inserted into {target_ref}")

        return {
            "table": target_ref,
            "dataset": dataset,
            "merge_status": "SUCCESS",
            "rows_affected": job.num_dml_affected_rows,
        }

    def _ensure_synthetic_column(self, table_cfg: dict, key_col: str) -> None:
        """Re-adds the synthetic key column to the target table (as
        nullable INT64) if a previous COMPLETED run already dropped it —
        see drop_synthetic_column. Existing rows get NULL for it, which
        is fine for the MERGE's ON clause (NULL never equals anything,
        so old rows simply never re-match); it does mean a synthetic key
        only gives a hard duplicate guarantee within a single completed
        load, not across repeated incremental syncs of a no-primary-key
        table, since the SQL Server shadow table backing it is rebuilt
        (and renumbered) from scratch whenever it doesn't already exist.
        """
        table_name = table_cfg["name"]
        schema_name = table_cfg["schema"]
        dataset = self._dataset(table_cfg)
        target_ref = f"{self.project_id}.{dataset}.{table_name}"

        table = self.client.get_table(target_ref)
        if key_col in [f.name for f in table.schema]:
            return

        print(f"[bq_merge] '{key_col}' missing from {target_ref} (a previous COMPLETED "
              f"run already dropped it for {schema_name}.{table_name}) — re-adding as "
              f"nullable so this run's merge can match on it ...")
        job = self.client.query(f"ALTER TABLE `{target_ref}` ADD COLUMN `{key_col}` INT64")
        job.result()
        print(f"[bq_merge] Re-added '{key_col}' to {target_ref}")

    def drop_synthetic_column(self, table_cfg: dict) -> None:
        """Called once by table_pipeline.py right after a synthetic-key
        table's checkpoint reaches COMPLETED (mirrors
        SqlExtractor.drop_synthetic_key_table doing the equivalent
        cleanup on the source side). The source_row_id column exists only
        to make batching/resuming/merging possible for a table with no
        real primary key — it's internal plumbing and must not be part
        of the table anyone downstream actually queries.

        Only the TARGET table is touched — staging is a transient scratch
        table, truncated and reloaded fresh every batch and never read by
        anyone outside this pipeline, so leaving the column there is
        harmless and not worth an extra DDL call.
        """
        table_name = table_cfg["name"]
        schema_name = table_cfg["schema"]
        key_col = table_cfg["primary_key"][0]
        dataset = self._dataset(table_cfg)
        target_ref = f"{self.project_id}.{dataset}.{table_name}"

        table = self.client.get_table(target_ref)
        if key_col not in [f.name for f in table.schema]:
            print(f"[bq_merge] '{key_col}' already absent from {target_ref} — nothing to drop")
            return

        print(f"[bq_merge] Dropping internal column '{key_col}' from {target_ref} "
              f"({schema_name}.{table_name}) ...")
        job = self.client.query(f"ALTER TABLE `{target_ref}` DROP COLUMN `{key_col}`")
        job.result()
        print(f"[bq_merge] Dropped '{key_col}' from {target_ref} — "
              f"table now matches its public (non-synthetic) column set")