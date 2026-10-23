# v3 — raw views / stored procedures + resource-group/server secrets

## New: raw migration of views and stored procedures (`--objects`)

* `python3 main.py --objects` (or `--views`, `--procedures`, `--objects functions triggers`,
  `--objects all`) exports the **original T-SQL** of every selected object — not translated.
  Selection works like tables: `--database`, `--server`, `--schema`, `--object-names`
  (`dbo.vw_*`, `SalesDB/dbo.usp_Load`), `--exclude-objects`, `--dry-run`, `--list`, `--spec`
  (`objects:` / `object_names:` / `exclude_objects:` job keys) and the Airflow conf.
* Read from `sys.objects`, `sys.sql_modules`, `sys.parameters`, `sys.columns` and
  `sys.sql_expression_dependencies` — the index first (cheap; used by `--dry-run`), definitions
  in batches only for what is selected.
* Written three ways: `.sql` files byte for byte (local `output/sql_objects/...` and
  `gs://<landing bucket>/sql_objects/...`, plus `manifest.json` in deploy order), and BigQuery
  `migration_sql_objects` (latest, MERGE) + `migration_sql_object_history` (every version).
* Change detection by SHA-256: NEW / CHANGED / UNCHANGED / DROPPED; unchanged files are not
  re-uploaded; full-database runs mark deleted objects `DROPPED_IN_SOURCE`.
* Prepared for the translation phase: `translation_status = PENDING`, empty `translated_sql` /
  `translated_at` / `bq_object` / `translation_notes`, `dependency_level` (deploy order, cycles
  cut), `references_cross_database`, parameters and view columns as JSON.
* Encrypted objects and missing `VIEW DEFINITION` are reported per object instead of failing.
* Per-database lock (`objects:<rg>/<server>/<db>`), concurrent-MERGE retry, `--stage gcs` = files only.
* New modules: `src/objects/` (`object_types`, `object_catalog`, `object_pipeline`, `object_store`).

## New: secrets named after resource group + server

* `.env` names like `INGREDION_DEV_RG_CENTRAL_INDIA_AZURE_SQL_INGREDION_DEV_SERVER_CENTRAL_INDIAA_USERNAME`
  (`<RG>_AZURE_SQL_<SERVER>_<FIELD>`) are looked up first; the older `AZURE_SQL_PASSWORD_<SERVER>`
  style and a shared `AZURE_SQL_PASSWORD` still work. The order is configurable
  (`secrets.sql_name_templates`, `secrets.storage_name_templates`).
* Names that differ only in underscores (a typo like `..._EAST_USAZURE_SQL_...`) are accepted
  with a warning (`secrets.fuzzy_match`).
* Storage secrets also accept `<RG>_AZURE_SAS_TOKEN` / `<RG>_AZURE_STORAGE_CONNECTION_STRING`;
  a leading `?` on the SAS token is stripped.
* `python3 main.py --check-connections`: per database, which variable each secret came from,
  a login test, table/view/procedure counts, `VIEW DEFINITION` check, SAS-token expiry.

## Other

* `run_items` and the object runner share one parallel executor (`--parallel`, `--max-per-server`).
* 175 offline tests (26 new: object catalog SQL parsed as T-SQL, MERGE parsed as BigQuery SQL,
  change detection, files byte for byte, CLI, DAG conf, secret names, connection check).
* Nothing changed for table migration; existing control tables are untouched (the two object
  tables are created on the first `--objects` run).

---

# What changed in this version (and why)

This version implements the fixes from the architecture review and adds a flexible
selection / load-mode CLI. Scope is unchanged: **tables only**.

## Fixes from the review

| # | Issue | Fix | Where |
|---|---|---|---|
| 1 | One Storage Transfer job + one BigQuery load per 25k-row batch (BigQuery 1,500 loads/table/day limit; minutes of overhead per batch) | Work is grouped into **chunks** (default 40 × 50,000 rows): one transfer job, one wildcard load and one MERGE per chunk | `pipeline/table_pipeline.py`, `transfer/transport.py`, `bigquery/bq_loader.py` |
| 2 | Data crossed clouds three times; one staging account for every region | `transfer.mode: direct` (GCS upload) or `sts` (run in Azure); per-location staging accounts; staged blobs deleted after transfer | `transfer/transport.py`, `azure/blob_uploader.py` |
| 3 | "Incremental" re-read the whole table every run; static cutoff blocked new rows; updates never applied | Real watermark (rowversion / updated-at / increasing key), window `(low, high]`, upsert MERGE; cutoff applies to full loads only; first run continues after MAX in BigQuery | `planner/table_planner.py`, `extraction/sql_extractor.py`, `metadata/checkpoint_manager.py` |
| 4 | No protection against two runs on one table | Per-table lease lock in BigQuery (`migration_lock`), renewed per chunk | `metadata/lock_manager.py` |
| 5 | No-PK tables duplicated on re-runs; shadow table copied into production by default; names over 128 chars | Fresh full loads truncate the target; default `no_pk_strategy: stream` (no source writes); unique indexes and `key_columns` overrides used before any synthetic key; shadow names hashed to fit | planner, pipeline, `discovery/shadow_table_naming.py` |
| 6 | `datetimeoffset` / `sql_variant` crashed pyodbc; spatial/hierarchyid bytes vs STRING; datetime shifted as UTC | Converted in SQL Server (`select_expr`), explicit Arrow schema, DATETIME for zone-less types | `planner/type_mapper.py`, `extraction/parquet_writer.py` |
| 7 | `--force` didn't reload | Fresh full load = truncate (recreate on type change) | pipeline |
| 8 | Datetime key resume broke (9-digit fraction) | Typed checkpoint values + parameters CAST to the column type | `common/values.py`, `extraction/sql_extractor.py` |
| 9 | No reconciliation | Source COUNT vs BigQuery rows after every full load; staged rows verified per chunk | pipeline |
| 10 | Source schema changes broke the MERGE | New columns added automatically; type changes reported (full reload recreates) | `bigquery/bq_loader.py` |
| 11 | Geo-replicas / dedicated pools discovered; case-sensitive control keys | Skipped by default; control keys lower-cased | `topology/azure_discovery.py`, `bigquery/bq_control_tables.py` |
| 12 | Airflow: big XCom, 1024 map limit, overlapping runs | Slim items re-planned per task, `max_active_runs=1`, documented `max_map_length` | `dags/`, `orchestration/dag_tasks.py` |
| 13 | Serial CLI, no per-server limit | `--parallel` with `--max-per-server` | `orchestration/runner.py` |
| 14 | Files never cleaned up | Local / Azure deleted per chunk, GCS lifecycle rule, staging dropped | pipeline, `gcs/gcs_manager.py` |
| 15 | One unmappable database stopped all | Reported and skipped (`--strict` to fail) | `topology/targets.py` |
| 16 | Secrets only as env vars; SQL logins only | Secret Manager provider; Entra service principal / managed identity auth | `config/connection.py` |
| 17 | 2 catalog queries per table | 3 queries per database (columns, keys, row estimates) from `sys.*` | `catalog/catalog_reader.py` |
| 18 | `print()` logging, unpinned deps, one COUNT per table in reports | Structured logging with thread names, pinned requirements, `__TABLES__` counts | `common/log.py`, `requirements.txt`, `reporting/` |

## New: flexible selection and modes

* `--tables` (one or many, wildcards, `database/schema.table`, `server/database/schema.table`),
  `--tables-file`, `--exclude-tables`, `--schema`, `--database` (one or many), `--server`,
  `--resource-group`, `--azure-location`, `--all`.
* `--mode auto | full | incremental | partial`, `--where`, `--date-column/--from/--to`,
  `--replace-range`, `--cutoff-date`, `--force`, `--incremental-only`.
* `--spec run.yaml`: several jobs (different scopes and modes) in one run.
* `--dry-run`, `--list`, `--parallel`, `--max-per-server`, `--strict`.

## Moving from the previous version

* Control tables are upgraded in place (new columns added). Checkpoints written by the old
  version have no `scope_key`, so the first run of each table starts fresh — for full loads
  that means truncate + reload.
* Targets created with `datetime` columns as TIMESTAMP now differ from the new DATETIME
  mapping: a full reload recreates them, incremental runs stop with a clear message. To keep the
  old behaviour set `types.datetime_as: TIMESTAMP`.
* `extraction.cutoff_date` now defaults to `null`; pass `--cutoff-date` to use one.
* `dags/migration_dag.py` is now the DAG `migration_run` (was `migration_full_load`).

## Before production

The package is tested offline (149 tests; SQL Server, GCS, Storage Transfer and BigQuery
faked; generated SQL parsed). It has not been run against your Azure/GCP environment here. Suggested
first run:

1. `python3 main.py --show-topology` and `--list` — check names, keys, watermarks, skips.
2. `python3 main.py --tables SalesDB/dbo.<small table> --mode full` — check the BigQuery table.
3. Same table with `--mode incremental` twice (second run should say NO_CHANGES).
4. One database with `--all --database SalesDB --parallel 2`, then widen.
