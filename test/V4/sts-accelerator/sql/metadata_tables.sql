-- Reference only — src/bigquery/bq_control_tables.py creates (and upgrades) these in
-- <project>.migration_control_<location>. Keys are lower-cased.

CREATE TABLE IF NOT EXISTS migration_checkpoint (
  resource_group STRING, source_server STRING, source_database STRING, gcp_dataset STRING,
  table_name STRING NOT NULL, schema_name STRING,
  scope_key STRING,              -- full | incremental | partial:<hash>
  load_mode STRING,
  status STRING,                 -- IN_PROGRESS | COMPLETED | FAILED
  last_pk_json STRING,           -- typed JSON of the last committed key
  last_batch_index INT64,        -- last committed chunk
  rows_done INT64,
  low_watermark_json STRING, high_watermark_json STRING,
  watermark_json STRING,         -- COMPLETED incremental: where the next run starts
  run_id STRING, updated_at TIMESTAMP
);

CREATE TABLE IF NOT EXISTS migration_audit (
  resource_group STRING, source_server STRING, source_database STRING, gcp_dataset STRING,
  run_id STRING, table_name STRING, schema_name STRING, bq_table STRING,
  load_mode STRING, scope_key STRING,
  outcome STRING,                -- SUCCESS | RESUMED_SUCCESS | FAILED | SUCCESS_GCS_ONLY | NO_CHANGES
  batches_processed INT64,       -- chunks
  rows_processed INT64, resumed_from_batch INT64,
  source_rows INT64, target_rows INT64, reconciled BOOL,
  watermark_json STRING, started_at TIMESTAMP, finished_at TIMESTAMP, error_message STRING
) PARTITION BY DATE(started_at);

CREATE TABLE IF NOT EXISTS migration_pipeline_logs (
  resource_group STRING, source_server STRING, source_database STRING, gcp_dataset STRING,
  run_id STRING, table_name STRING, schema_name STRING, scope_key STRING,
  stage STRING, status STRING, batch_index INT64, rows_processed INT64, bytes_processed INT64,
  started_at TIMESTAMP, finished_at TIMESTAMP, error_message STRING, extra_json STRING
) PARTITION BY DATE(started_at);

CREATE TABLE IF NOT EXISTS migration_lock (
  lock_key STRING NOT NULL,      -- rg/server/database/schema.table
  owner STRING, acquired_at TIMESTAMP, expires_at TIMESTAMP
);

-- ---- Raw views / stored procedures (src/objects/object_store.py) -------------------------
CREATE TABLE IF NOT EXISTS migration_sql_objects (
  resource_group STRING, source_server STRING, source_database STRING, gcp_dataset STRING,
  object_schema STRING, object_name STRING,
  object_type STRING,            -- view | procedure | function | trigger
  sql_type_code STRING, sql_type_desc STRING, parent_object STRING,
  definition STRING,             -- ORIGINAL T-SQL, byte for byte (NULL when encrypted / no permission)
  definition_sha256 STRING, definition_length INT64,
  definition_status STRING,      -- OK | ENCRYPTED | NO_PERMISSION
  uses_ansi_nulls BOOL, uses_quoted_identifier BOOL, is_schema_bound BOOL,
  parameters_json STRING, returns_json STRING, columns_json STRING, references_json STRING,
  references_cross_database BOOL,
  dependency_level INT64,        -- deploy order for the translation phase (0 first)
  source_created_at TIMESTAMP, source_modified_at TIMESTAMP,
  gcs_uri STRING,
  change_status STRING,          -- NEW | CHANGED | UNCHANGED | DROPPED (last run)
  translation_status STRING,     -- PENDING | NOT_AVAILABLE   (filled later by the translation phase)
  run_id STRING,
  source_status STRING,          -- PRESENT | DROPPED_IN_SOURCE
  first_seen_at TIMESTAMP, last_extracted_at TIMESTAMP, last_changed_at TIMESTAMP,
  translated_sql STRING, translated_at TIMESTAMP, bq_object STRING, translation_notes STRING
);

CREATE TABLE IF NOT EXISTS migration_sql_object_history (
  resource_group STRING, source_server STRING, source_database STRING, gcp_dataset STRING,
  object_schema STRING, object_name STRING, object_type STRING,
  change_status STRING,          -- NEW | CHANGED | DROPPED
  definition STRING, definition_sha256 STRING, definition_status STRING,
  source_modified_at TIMESTAMP, gcs_uri STRING, extracted_at TIMESTAMP, run_id STRING
) PARTITION BY DATE(extracted_at);

-- Handy queries
--   every pending procedure of one database, in deploy order:
--     SELECT object_schema, object_name, definition FROM migration_sql_objects
--     WHERE source_database = 'salesdb' AND object_type = 'procedure' AND translation_status = 'PENDING'
--       AND source_status = 'PRESENT' ORDER BY dependency_level, object_schema, object_name;
--   what changed in Azure since the last run:
--     SELECT * FROM migration_sql_objects WHERE change_status IN ('NEW', 'CHANGED', 'DROPPED');
