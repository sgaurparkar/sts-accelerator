-- Reference copy only — src/bigquery/bq_control_tables.py creates all
-- three of these with CREATE TABLE IF NOT EXISTS at the start of every
-- table's pipeline run, so they always exist with no manual setup and
-- no hand-maintained DDL. There is no `migration_control` table
-- anymore — table configuration is no longer stored in BigQuery OR in
-- a YAML list; it's discovered live from SQL Server every run (see
-- src/planner/table_planner.py). These three tables are purely
-- pipeline-generated operational state:

-- Raw per-stage-event log, as a queryable table (mirrors the JSON
-- objects also written to GCS — see src/metadata/metadata_manager.py).
CREATE TABLE IF NOT EXISTS migration_pipeline_logs (
    run_id            STRING,
    table_name        STRING,
    schema_name       STRING,
    stage             STRING,   -- extraction | upload | transfer | load | pipeline
    status            STRING,   -- STARTED | SUCCESS | FAILED
    batch_index       INT64,
    rows_processed    INT64,
    bytes_processed   INT64,
    started_at        TIMESTAMP,
    finished_at       TIMESTAMP,
    error_message     STRING,
    extra_json        STRING
)
PARTITION BY DATE(started_at);

-- Last committed primary key per table — this is the failure-recovery
-- checkpoint. Append-only: each batch writes a new row rather than
-- updating in place, so it doubles as a history of every checkpoint
-- ever committed for a table.
CREATE TABLE IF NOT EXISTS migration_checkpoint (
    table_name        STRING NOT NULL,
    schema_name       STRING,
    last_pk_json      STRING,   -- JSON object of primary-key column(s) -> value
    last_batch_index  INT64,
    status            STRING,   -- IN_PROGRESS | COMPLETED | FAILED
    run_id            STRING,
    updated_at        TIMESTAMP
);

-- One row per table per run — the human-readable audit trail: did this
-- table's run succeed, how many rows/batches, did it resume a prior
-- failure, and from which batch.
CREATE TABLE IF NOT EXISTS migration_audit (
    run_id              STRING,
    table_name          STRING,
    schema_name         STRING,
    load_mode           STRING,
    outcome             STRING,  -- SUCCESS | FAILED | RESUMED_SUCCESS
    batches_processed   INT64,
    rows_processed      INT64,
    resumed_from_batch  INT64,
    started_at          TIMESTAMP,
    finished_at         TIMESTAMP,
    error_message       STRING
)
PARTITION BY DATE(started_at);
