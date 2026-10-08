# Migration Accelerator — Python + Airflow Edition

Fully code-driven, fully dynamic version of the accelerator: real
Python modules per concern, orchestrated by two Airflow DAGs, designed
to run in Cloud Composer (no local machine required — build/test it
via Google Cloud Shell). Talks only to real SQL Server — the earlier
SQLite demo path has been removed entirely.

## What "dynamic" means here

Nothing about which schemas or tables to migrate, their columns, or
their primary keys is hand-typed anywhere in this repo:

- **Schemas** — leave `source.schemas` empty in `config/settings.yaml`
  and `src/discovery/table_discovery.py` discovers every schema that
  owns at least one base table straight off SQL Server (system/security
  schemas excluded), so `python main.py --all` and both DAGs cover a
  brand-new schema the moment it exists in the source database — zero
  config change. Set `source.schemas` to an explicit list to scope the
  pipeline to only those schemas instead. `--schema <name>` on the CLI
  scopes a single run (`--all`, `--list`) to one schema, and
  disambiguates `--table <name>` when the same table name exists in
  more than one schema (or just pass `--table schema.table`).
- **Tables & primary keys** — `src/discovery/table_discovery.py` reads
  `INFORMATION_SCHEMA` / key-constraint catalog views straight off SQL
  Server every run. Add a table to the source database and it's picked
  up on the next run with zero code or config change.
- **load_mode** — every table's planned mode is `"full"` by default
  (`src/planner/table_planner.py`), meaning it's picked up by
  `migration_dag.py`. Override a table to `"incremental"` in
  `config/tables_override.yaml` to have it picked up by
  `incremental_dag.py`'s schedule instead, to periodically re-check for
  newly appended rows. `config/tables_override.yaml` is *optional* —
  only touch it to force a specific table's load_mode, or exclude a table.
- **Insert-only, no watermark column** — the source data is static
  (existing rows never change, only new ones get appended), so there
  is no watermark column anywhere in this pipeline. Every MERGE
  matches purely on primary key and only has a `WHEN NOT MATCHED THEN
  INSERT` branch — rows already present in the target are left
  completely untouched, regardless of load_mode. See
  `src/bigquery/bq_merge.py`.
- **No partitioning or clustering** — target and staging tables are
  created as plain BigQuery tables.
- **`config/tables.yaml`** — a read-only snapshot of the current plan
  (table, primary key, load_mode), rewritten by `table_planner.py` on
  every run purely for visibility. Never hand-edit it and nothing reads
  it back in — delete it any time, it just reappears next run.
- **Batching, not partitioning** — large tables are no longer split by
  ranges of a business column. Every table is paged by its own primary
  key (keyset/seek pagination, `src/extraction/sql_extractor.py`), so
  one Parquet file is produced per page (`table_part0000.parquet`,
  `table_part0001.parquet`, ...), each capped at
  `extraction.batch_size` rows in `config/settings.yaml`.
- **BigQuery objects** — target table, staging table, and the three
  operational tables below are all created with `CREATE TABLE IF NOT
  EXISTS` from a schema built dynamically off the source (see
  `src/bigquery/bq_control_tables.py`). Target/staging tables are plain
  — no partitioning, no clustering.

## Choosing the source region

The source SQL servers are one-per-Azure-region and listed under `regions:` in
`config/settings.yaml` (fill in each server's `<name>.database.windows.net`).
Pass the region exactly as Azure shows it — quotes are optional, and case,
spaces and hyphens are ignored:

```bash
python3 main.py --list-regions
python3 main.py --all --region North Central US
python3 main.py --table dbo.customers --region "Brazil South"
python3 main.py --list --region west us 2
python3 main.py --all --region East US gcs        # stage word may follow the region
python3 main.py --all --region all                # every region that has a host set
python3 main.py --report --region Southeast Asia
```

`--region` is required with `--table`, `--all`, `--list` and `--report`. With
`--region all`, regions run one after another; a failing region doesn't stop the
others, and the exit code is 1 if any failed. Regions whose host is still a
`<placeholder>` are skipped (with a warning) under `all`, and rejected if named.

**Airflow:** trigger `migration_full_load` with conf `{"region": "East US"}` (or
`"all"`), optionally plus `"force": true`. `migration_incremental` runs **every**
configured region on its schedule unless you trigger it with a region (or set the
`SOURCE_REGION` env var). A bad region fails the task immediately, without retries.

### Region-wise layout in GCP

Each Azure region lands in its own, separate resources, created in the GCP
location set by `regions.<name>.gcp_location` (required):

| What | Name for `--region "East US"` |
|---|---|
| GCS landing bucket (auto-created) | `migration-landing-bucket-east-us` |
| Data datasets | `east_us_dbo`, `east_us_sales`, ... |
| Control tables (checkpoint / audit / logs / report) | dataset `migrated_data_east_us` |
| Azure staging path | `<container>/east_us/<schema>/<table>/` |
| Local Parquet | `data/parquet/east_us/<schema>/` |
| Plan snapshot | `config/tables_east_us.yaml` |

Names can be overridden per region with `gcs_bucket` / `bq_dataset`. Existing
bucket/datasets in a *different* location than `gcp_location` are rejected up
front (BigQuery can't load across locations). The first run for a region creates
its bucket (or create them with `terraform/gcp/regional.tf` — it also grants the
permission below; use one approach or the other, see the comments in that file); give the Storage Transfer Service account
(`project-<PROJECT_NUMBER>@storage-transfer-service.iam.gserviceaccount.com`)
`Storage Legacy Bucket Writer` + `Storage Object Viewer` on it once, unless your
project already grants that project-wide.

### Per-region credentials

Servers in different regions often have different logins. For each secret, a
region-specific variable wins over the shared one (see `.env.example`):

```
AZURE_SQL_USERNAME_EAST_US / AZURE_SQL_PASSWORD_EAST_US        -> else AZURE_SQL_USERNAME / AZURE_SQL_PASSWORD
AZURE_STORAGE_CONNECTION_STRING_EAST_US, AZURE_SAS_TOKEN_EAST_US -> else the shared ones
```

### Region-scoped overrides

An entry in `config/tables_override.yaml` may add `region: East US` so it applies
to that region only (a region-specific entry beats a general one).

## How the pieces fit together

```
config/settings.yaml + tables_override.yaml (optional) + logging.yaml   <- how to connect & optional overrides
        │
        ▼
src/discovery, src/planner                          <- reads live SQL Server schema, PKs, builds the per-table
                                                         plan (load_mode "full" by default) and writes
                                                         config/tables.yaml as a read-only snapshot of it
        │
        ▼
src/extraction (sql_extractor, parquet_writer)       <- pulls one PK-paginated batch at a time, writes Parquet
        │
        ▼
src/azure (blob_uploader)                            <- uploads that batch to Azure Blob staging
        │
        ▼
src/sts (sts_client, sts_job_manager)                <- moves Blob -> GCS
        │
        ▼
src/gcs (gcs_manager)                                <- verifies landing, cleans up after
        │
        ▼
src/bigquery (bq_control_tables, bq_merge)           <- ensures tables exist, loads to staging, MERGEs into target
        │
        ▼
src/metadata (metadata_manager, checkpoint_manager)  <- logs every stage event (GCS + BigQuery table),
                                                         commits a resume checkpoint after every batch
        │
        ▼
src/reporting (report_generator)                     <- rolls up migration_audit into one row per
                                                         table (latest status + live row count) —
                                                         writes it as a real BigQuery table AND a
                                                         local JSON snapshot under logs/reports/

src/pipeline/table_pipeline.py  <- the one place the whole chain above is wired together;
                                    main.py and both DAGs all call this, so there's one code path, not three.

dags/migration_dag.py       <- discovers full-load tables at task runtime, fans out with dynamic task
                                mapping, then generates the tablewise report once every table is done
dags/incremental_dag.py     <- same, for incremental tables (staging + insert-only merge on a schedule)
main.py                     <- runs one table through the chain manually (or --report / --list), no Airflow needed
```

## Failure recovery (checkpoint + insert-only MERGE)

Every batch is loaded to a staging table and then `MERGE`d into the
target on the table's real (possibly composite) primary key — never a
truncate-and-reload, and with no `WHEN MATCHED` branch at all (see
"Insert-only" above). After each batch's MERGE succeeds,
`checkpoint_manager.py` commits the last primary key seen to the
`migration_checkpoint` BigQuery table.

If a table's run fails partway through, the next cycle reads that
checkpoint and resumes extraction right after the last committed
primary key — it does not restart the table from batch 0. Because the
MERGE is insert-only and matches on primary key, re-running (resuming)
a batch that already landed is always a safe no-op: every row in it
already exists in the target by primary key, so nothing gets
duplicated or overwritten. `migration_audit` records one row per table
per run showing whether that run was a fresh `SUCCESS`, a
`RESUMED_SUCCESS` (picked up after a prior failure), or a `FAILED` run.

## Logs and reporting

Three layers, from most granular to most readable:

1. **Raw events (JSON)** — one immutable JSON object per stage event,
   under `logs/raw/<run_id>/<event_id>.json` locally, or
   `gcs://<bucket>/pipeline_logs/raw/<run_id>/<event_id>.json` in
   production (default; survives across Composer workers).
2. **`migration_pipeline_logs`** (BigQuery table) — the same events,
   queryable with SQL instead of grepping JSON files.
3. **`migration_report`** (BigQuery table) — the tablewise summary:
   one row per table showing its latest run's outcome plus its actual
   current row count in BigQuery right now. Regenerated fresh (not
   appended) every time it runs — `src/reporting/report_generator.py`.
   Also drops a dated local JSON copy under `logs/reports/`.

Generate/refresh the report any time with:
```
python3 main.py --report
```
It also runs automatically as the last task of `migration_dag.py`,
after every table in that run has finished (success or failure).

## Running this without a local machine (Cloud Shell only)

1. Open console.cloud.google.com → activate Cloud Shell (top-right terminal icon).
2. Upload or clone this project into Cloud Shell's home directory (drag-and-drop into
   the Cloud Shell file browser, or `git clone` if it's in a repo).
3. `pip install -r requirements.txt`
4. Fill in `.env` (copy from `.env.example`) with your real SQL Server/Azure/GCP values.
5. See what gets discovered before running anything: `python3 main.py --list`
   (add `--schema dbo` to see just one schema)
6. Test one table manually: `python3 main.py --table customers`
   — or `python3 main.py --table customers --schema dbo` / the
   `python3 main.py --table dbo.customers` shorthand if the same table
   name exists in more than one schema
7. Migrate every table in every discovered schema: `python3 main.py --all`
   (add `--schema dbo` to scope a run to just one schema)
7. Once that works, deploy the DAGs to Composer:
   ```
   gcloud composer environments storage dags import \
     --environment <your-composer-env> --location <region> \
     --source dags/migration_dag.py
   ```
   Also upload the `src/` and `config/` folders into Composer's DAG bucket
   (same bucket, alongside `dags/`) since the DAG files import from them.
8. Trigger the DAG from the Airflow UI (linked from your Composer environment
   in the GCP Console) and watch it run.
