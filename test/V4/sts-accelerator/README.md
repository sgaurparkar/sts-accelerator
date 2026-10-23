# Migration Accelerator — Azure SQL → BigQuery (tables + raw views / stored procedures)

Moves tables of Azure SQL databases into BigQuery, keeping the Azure hierarchy and each
server's region, and migrates the **original T-SQL of views and stored procedures** (raw,
not translated yet). Nothing about *which* databases, tables or objects exist is hand-typed:
the Azure hierarchy comes from Azure Resource Manager, tables/columns/keys/views/procedures
come from each database's catalog, and every GCP name is derived from them.

**Scope:** table data · raw view / procedure (optionally function / trigger) definitions.
Not yet: translating T-SQL into BigQuery SQL (planned next phase — the raw rows are already
marked `translation_status = PENDING` for it), users, indexes.

## Azure → GCP mapping

| Azure | GCP | Example |
|---|---|---|
| Resource Group | Project (created if missing) | `ResourceGroup1` → `resourcegroup1` |
| SQL Server + Database | BigQuery dataset | `SQLServer1` / `SalesDB` → `sqlserver1_salesdb` |
| Schema.Table | Table in that dataset | `dbo.Orders` → `Orders`, `sales.Orders` → `sales__Orders` |
| Server's Azure location | Dataset + bucket location | Central India → `asia-south1`; East US → `us-east4` |

```
python3 main.py --show-topology        # discover Azure and print the full mapping — changes nothing
```

## Quick start

```bash
bash setup.sh                                   # venv + Python deps + ODBC driver 18
cp .env.example .env                            # SQL logins per RG/server, storage secrets, Azure SP
#  edit config/settings.yaml: gcp.projects.parent / billing_account / id_prefix, transfer.mode, azure.*
python3 main.py --show-topology                 # what maps where
python3 main.py --check-connections             # which .env variable each server uses + login test
python3 main.py --provision                     # projects, billing, APIs, buckets, datasets, control tables
python3 main.py --list                          # every table, its BigQuery name, key, strategy
python3 main.py --all --dry-run                 # the full plan, nothing migrated
python3 main.py --all --parallel 4              # migrate everything in scope
python3 main.py --objects --dry-run             # views + procedures that would be migrated (raw)
python3 main.py --objects                       # migrate them (original T-SQL, not translated)
```

---

## Command cookbook

Every command below can add `--dry-run` (plan only) or use `--list` instead of migrating.
Table patterns are case-insensitive and accept wildcards (`*`, `?`, `[..]`).

### 1. Choose WHAT to migrate

| I want to migrate… | Command |
|---|---|
| **one table** (in every database that has it) | `python3 main.py --tables dbo.Orders` |
| one table of **one database** | `python3 main.py --tables dbo.Orders --database SalesDB`  *or*  `--tables SalesDB/dbo.Orders` |
| one table on one **server** + database | `python3 main.py --tables sqlserver1/SalesDB/dbo.Orders` |
| a **list of tables** | `python3 main.py --tables dbo.Orders dbo.Customers sales.Invoices` (or comma-separated) |
| a list from a **file** | `python3 main.py --tables-file config/tables.txt` (one pattern per line, `#` comments) |
| tables by **wildcard** | `python3 main.py --tables "dbo.Order*" "sales.*"` |
| **everything except** some | `python3 main.py --all --exclude-tables "dbo.tmp_*" "*.bak_*"` |
| one **schema** | `python3 main.py --all --schema sales` |
| one whole **database** | `python3 main.py --all --database SalesDB` |
| **several databases** | `python3 main.py --all --database SalesDB HRDB` |
| tables from **different databases** in one run | `python3 main.py --tables SalesDB/dbo.Orders HRDB/dbo.Employees FinanceDB/dbo.*` |
| one **resource group** | `python3 main.py --all --resource-group ResourceGroup1` |
| one **server** | `python3 main.py --all --server sqlserver3` |
| one **Azure region** | `python3 main.py --all --azure-location "Central India"` |
| **everything** in scope | `python3 main.py --all` |

Pattern forms: `Orders` · `dbo.Orders` · `[dbo].[Order Details]` · `SalesDB/dbo.Orders` ·
`sqlserver1/SalesDB/dbo.Orders` · `ResourceGroup1/sqlserver1/SalesDB/dbo.*`.
An exact pattern that matches nothing is reported as `NOT FOUND` (with `--strict` it fails the run).

### 1b. Raw views and stored procedures (`--objects`)

| I want to migrate… | Command |
|---|---|
| **all views + procedures** of every database in scope | `python3 main.py --objects` |
| only **views** / only **procedures** | `python3 main.py --views`  ·  `python3 main.py --procedures` |
| views + procedures of **one database** | `python3 main.py --objects --database SalesDB` |
| **named** objects / wildcards | `python3 main.py --objects --object-names dbo.vw_Sales "dbo.usp_Load*"` |
| objects of different databases | `python3 main.py --procedures --object-names SalesDB/dbo.usp_A HRDB/hr.*` |
| everything except some | `python3 main.py --objects --exclude-objects "dbo.tmp_*"` |
| functions / triggers too | `python3 main.py --objects views procedures functions triggers` (or `--objects all`) |
| **tables and objects** in one run | `python3 main.py --all --objects --database SalesDB` |
| see first, migrate nothing | add `--dry-run` (or `--list`) |
| only files, no BigQuery rows | add `--stage gcs` |
| re-upload unchanged files | add `--force` |

`--object-names` uses exactly the table pattern syntax (`schema.name`, `db/schema.name`,
`server/db/schema.name`, wildcards). See [Raw views and stored procedures](#raw-views-and-stored-procedures).

### 2. Choose HOW to load (`--mode`)

| Mode | What it does | Example |
|---|---|---|
| `auto` (default) | per table from `config/tables_override.yaml` (`load_mode`), otherwise `full` | `python3 main.py --all` |
| `full` | complete copy. A **fresh** run truncates the BigQuery table first (recreates it if column types changed); a **failed** run resumes after its last committed chunk; a **COMPLETED** table is skipped | `python3 main.py --all --database SalesDB --mode full` |
| `full --force` | reload even if already COMPLETED | `python3 main.py --tables dbo.Orders --mode full --force` |
| `full` + cutoff | only rows up to a date (column `extraction.cutoff_column`) | `python3 main.py --all --mode full --cutoff-date 2026-09-22` |
| `incremental` | only rows **changed since the last successful run**, upserted by key | `python3 main.py --all --database HRDB --mode incremental` |
| `incremental --force` | re-read every row (upsert), then continue incrementally | `python3 main.py --tables dbo.Orders --mode incremental --force` |
| `partial` (filter) | rows matching a SQL predicate, upserted by key | `python3 main.py --tables dbo.Orders --mode partial --where "Region = 'IN'"` |
| `partial` (date range) | rows in `[--from, --to)`, upserted by key | `python3 main.py --tables dbo.Orders --mode partial --date-column OrderDate --from 2026-01-01 --to 2026-02-01` |
| `partial --replace-range` | first deletes that range in BigQuery, so deletes in the range are mirrored | `... --date-column OrderDate --from 2026-03-01 --to 2026-04-01 --replace-range` |
| only tables marked incremental | `--mode auto` limited to `load_mode: incremental` overrides | `python3 main.py --all --incremental-only` |

Mode aliases: `delta`/`cdc` → incremental, `filtered`/`subset` → partial, `reload`/`complete` → full.

**How incremental finds changes** (per table, automatically): the `watermark_column` override →
a `rowversion` column → the first column named in `incremental.watermark_columns`
(`updatedAt`, `ModifiedDate`, …) → a single ever-increasing key column (append-only). The first
incremental run continues after `MAX(watermark)` already in BigQuery (e.g. after a full load).
Each run reads the window `(last watermark, high watermark captured at start]`.
Tables with no key, or no possible watermark, are listed as `SKIP` with the reason.

### 3. Mix everything in one run (`--spec`)

```bash
python3 main.py --spec config/run_spec.example.yaml --dry-run
python3 main.py --spec config/run_spec.example.yaml --parallel 4
```

```yaml
defaults: {mode: full}
jobs:
  - name: sales-full                      # a whole database, full
    databases: [SalesDB]
    all_tables: true
    exclude_tables: ["dbo.tmp_*"]
  - name: hr-incremental                  # a list of tables, incremental
    databases: [HRDB]
    tables: [dbo.Employees, dbo.Payroll]
    mode: incremental
  - name: finance-march                   # one month of one table, including deletes
    databases: [FinanceDB]
    tables: [dbo.Transactions]
    mode: partial
    date_column: TxnDate
    from: 2026-03-01
    to: 2026-04-01
    replace_range: true
  - name: sales-raw-objects               # views + procedures, original T-SQL
    databases: [SalesDB]
    objects: [views, procedures]
    exclude_objects: ["dbo.tmp_*"]
```

### 4. Execution options

| Option | Meaning |
|---|---|
| `--parallel 4` | migrate 4 tables at once (largest tables start first) |
| `--max-per-server 2` | never more than 2 concurrent readers on one SQL server (default 2) |
| `--stage gcs` | stop once files are verified in GCS (no BigQuery load, no checkpoint) |
| `--skip-provision` | projects / buckets / datasets already exist |
| `--strict` | fail if a database can't be mapped or an exact table pattern matches nothing |
| `--ignore-locks` | skip the per-table lock (only when you are sure nothing else runs) |
| `--log-level DEBUG` | more detail |

Exit code: `0` = everything succeeded or was skipped, `1` = something failed, `2` = bad arguments.

---

## How a table is migrated

```
lock table ─► read checkpoint ─► skip? ─► prepare target ─► [incremental window]
   └─► per CHUNK (batches_per_chunk × batch_size rows, default 40 × 50,000 = 2M rows):
          SQL Server (keyset pages by PK/unique key, typed + CAST parameters)
          ─► Parquet files (explicit Arrow schema)
          ─► GCS: one Storage Transfer job for the chunk folder  |  or direct upload
          ─► ONE load job gs://…/chunk/*.parquet → staging (row count verified)
          ─► ONE MERGE (insert-only / upsert, de-duplicated) or INSERT → target
          ─► checkpoint (last key, rows, window) ─► renew lock ─► delete local/staged files
   └─► COMPLETED (+ watermark) ─► source vs BigQuery row count (full) ─► audit ─► unlock
```

* **Keys:** primary key → unique index (NOT NULL, unfiltered) → `key_columns` override. Tables
  with none: `extraction.no_pk_strategy` — `stream` (default: one forward-only read, nothing
  written to the source; full loads only), `shadow_table` (row-number copy in the source,
  resumable) or `exclude`.
* **Types:** `datetime`/`datetime2`/`smalldatetime` → `DATETIME` (wall clock, no zone shift);
  `datetimeoffset` → `TIMESTAMP` (UTC); `geography`/`geometry` → WKT; `hierarchyid` → `'/1/2/'`;
  `xml`/`sql_variant` → STRING; decimals keep their scale (`NUMERIC` or `BIGNUMERIC`).
  Column names are made BigQuery-safe (`Order Date` → `Order_Date`); `gcp.column_naming: keep` keeps them.
* **Schema changes:** new source columns are added to the target automatically; a changed type
  fails incremental/partial runs with a clear message and is fixed by `--mode full --force`.
* **Transfer** (`transfer.mode`): `sts` = Azure Blob staging (per-region account via
  `azure.storage_accounts_by_location`) + one Storage Transfer job per chunk — run the
  extractor in Azure for this; `direct` = straight to GCS — run it in GCP. Pick the one where
  the data crosses clouds once.
* **Locks:** one runner per table (BigQuery lease row, expires after `locking.ttl_seconds`), so
  the on-demand DAG, the incremental DAG and retries never collide.
* **Cleanup:** local Parquet and staged blobs are deleted after each chunk; landed GCS files
  expire via a bucket lifecycle rule (`gcp.landing_bucket.retention_days`); staging tables are
  dropped when the table finishes.

## Raw views and stored procedures

T-SQL does not run in BigQuery, so this phase **copies the definitions exactly as SQL Server
stores them** (comments, formatting and line endings included) and records everything the
translation phase will need. Nothing is created as a BigQuery view or routine yet.

```
lock objects:<rg>/<server>/<db>
 ─► sys.objects (index: what exists)  ─► sys.sql_modules (definitions)
     + sys.parameters + view columns + sys.sql_expression_dependencies
 ─► dependency_level per object (0 = depends only on tables; views before the procs that use them)
 ─► SHA-256 vs. BigQuery → NEW | CHANGED | UNCHANGED | DROPPED
 ─► files   output/sql_objects/<rg>/<server>/<db>/views/<schema>.<name>.sql   (+ manifest.json)
            gs://<landing bucket>/sql_objects/<rg>/<server>/<db>/procedures/<schema>.<name>.sql
 ─► BigQuery <project>.migration_control_<location>.migration_sql_objects        (MERGE, latest)
            <project>.migration_control_<location>.migration_sql_object_history  (every version)
 ─► unlock
```

* **Re-runs are cheap and safe:** unchanged objects are not re-uploaded; a changed definition
  is updated, its old version stays in the history table, and its `translation_status` goes
  back to `PENDING`. A full-database run marks objects deleted in Azure as `DROPPED_IN_SOURCE`
  (runs limited by `--object-names` / `--schema` never mark anything dropped).
* **Missing definitions** are reported, not fatal: `ENCRYPTED` (created `WITH ENCRYPTION`) or
  `NO_PERMISSION` (grant the login `VIEW DEFINITION`). `--check-connections` warns about both.
* **Ready for translation:** each row has `translation_status` (`PENDING` / `NOT_AVAILABLE`),
  empty `translated_sql`, `translated_at`, `bq_object`, `translation_notes`, plus parameters,
  view columns, references (`references_cross_database` flags `OtherDB.dbo.X` references) and
  `dependency_level` for the deploy order. `manifest.json` lists the objects in that order.
* Settings: `objects:` in `config/settings.yaml` (default types, local folder, GCS prefix,
  BigQuery on/off, drop marking). GCS `sql_objects/` files are **not** removed by the landing
  bucket's lifecycle rule (that only applies to `parquet/`).

```sql
-- every pending procedure of one database, in deploy order
SELECT object_schema, object_name, definition
FROM `<project>.migration_control_asia_south1.migration_sql_objects`
WHERE source_database = 'salesdb' AND object_type = 'procedure'
  AND translation_status = 'PENDING' AND source_status = 'PRESENT'
ORDER BY dependency_level, object_schema, object_name;
```

## Per-table overrides (optional)

Copy `config/tables_override.example.yaml` to `config/tables_override.yaml`. Keys: `exclude`,
`bq_table`, `load_mode`, `watermark_column`, `key_columns`, `no_pk_strategy`; scope an entry
with `resource_group` / `server` / `database` (most specific wins).

## Secrets (`.env`)

One SQL login per server, named after the resource group and the server (Azure names
upper-cased, `-`/`.`/spaces → `_`):

```dotenv
INGREDION_DEV_RG_CENTRAL_INDIA_AZURE_SQL_INGREDION_DEV_SERVER_CENTRAL_INDIAA_USERNAME=<sql login>
INGREDION_DEV_RG_CENTRAL_INDIA_AZURE_SQL_INGREDION_DEV_SERVER_CENTRAL_INDIAA_PASSWORD="..."
INGREDION_DEV_RG_EAST_US_AZURE_SQL_INGREDION_DEV_SERVER_EAST_US_USERNAME=<sql login>
INGREDION_DEV_RG_EAST_US_AZURE_SQL_INGREDION_DEV_SERVER_EAST_US_PASSWORD="..."
AZURE_SAS_TOKEN="sv=...&se=...&sig=..."
AZURE_STORAGE_CONNECTION_STRING="DefaultEndpointsProtocol=https;AccountName=...;AccountKey=...;EndpointSuffix=core.windows.net"
```

Names tried per server, first one set wins (`secrets.sql_name_templates`, editable):
`<RG>_AZURE_SQL_<SERVER>_<FIELD>` → `AZURE_SQL_<SERVER>_<FIELD>` → `AZURE_SQL_<FIELD>_<SERVER>` →
`AZURE_SQL_<FIELD>_<RG>` → `AZURE_SQL_<FIELD>_<LOCATION>` → `AZURE_SQL_<FIELD>`. Storage:
`<RG>_<NAME>` → `<NAME>_<ACCOUNT>` → `<NAME>_<LOCATION>` → `<NAME>`. A name that differs only in
underscores (`..._EAST_USAZURE_SQL_...`) is still accepted, with a warning naming the typo
(`secrets.fuzzy_match: false` to turn that off). Values come from `.env`, then Google Secret
Manager if `secrets.provider: gcp_secret_manager`. `source.auth: entra_service_principal` or
`entra_managed_identity` avoids SQL passwords entirely.

`python3 main.py --check-connections` shows, per database, which variable supplied each
secret (never the value), logs in, counts tables / views / procedures, checks
`VIEW DEFINITION`, and warns when the SAS token expires within 30 days.

## Airflow / Cloud Composer

* `migration_run` (on demand): trigger conf is one run-spec job, e.g.
  `{"databases": ["SalesDB"], "tables": ["dbo.Orders"], "mode": "full", "force": true}`
  or `{"spec": "config/run_spec.yaml"}`.
* The same conf can carry `objects`: `{"databases": ["SalesDB"], "objects": ["views", "procedures"]}`
  exports objects only; add `"all_tables": true` for tables too. One mapped task per database.
* `migration_incremental` (scheduled, `MIGRATION_INCREMENTAL_SCHEDULE`): tables marked
  `load_mode: incremental`, or every table with `MIGRATION_INCREMENTAL_ALL=1`.

Mapped tasks re-plan their table from the live catalog, so XCom stays small. Airflow maps at
most `[core] max_map_length` tasks (default 1024) — raise it for larger runs.
Upload `dags/`, `src/` and `config/` side by side to the DAG bucket, or set `STS_ACCELERATOR_HOME`.

## Logs and reports (in `<project>.migration_control_<location>`)

`migration_checkpoint` (resume state + watermarks) · `migration_audit` (one row per table per
run, incl. source/target row counts) · `migration_pipeline_logs` (events) · `migration_lock` ·
`migration_report` (latest outcome + live row count per table, rebuilt by `--report` and after
every run; JSON copy under `logs/reports/`) · `migration_sql_objects` / `migration_sql_object_history`
(raw view / procedure definitions, see above).

## Tests

```bash
python3 -m pytest -q        # offline (175 tests): SQL Server, GCS, Storage Transfer and BigQuery are faked;
                            # generated T-SQL and BigQuery SQL are checked with a SQL parser
```

## Known limits

* Views / procedures are migrated **raw**: their T-SQL is stored, not translated or deployed
  as BigQuery views / routines (next phase). Users, indexes and constraints are not migrated.
* Object definitions that SQL Server hides (`WITH ENCRYPTION`, or no `VIEW DEFINITION`) can't be
  exported; they are listed with that status.
* Incremental mode sees inserts and updates; **hard deletes** are not detected (use a periodic
  `--mode full --force`, or `--mode partial --replace-range` for date-partitioned data).
* A full load of a table that changes while it is read is not a point-in-time snapshot; the
  row-count check flags the difference.
* Very large single tables are read by one worker (no intra-table parallelism yet).
* Not yet run against live Azure / GCP from this package — start with `--dry-run`, then one
  small table, then one database (see CHANGES.md).
