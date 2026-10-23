# Multi-project mode: one GCP project per Azure resource group, created automatically

Off by default (`multi_project.enabled: false`) - nothing changes until you switch it on.

## What it does
Per configured target (a `regions:` entry with `resource_group` and `server`):

| Azure | GCP |
|---|---|
| resource group `rg-na-eastus` | project `rg-na-eastus` (created automatically, billed, APIs on). With `project_prefix: mig` it would be `mig-rg-na-eastus` |
| target label (`East US`) | bucket `mig-rg-na-eastus-east-us` (`parquet/<schema>/<table>/`) |
| server + database | ONE dataset `<server>_<database>` (e.g. `sql_server_01_salesdb`) |
| schema + table | table: `dbo.Orders` -> `Orders`; `sales.Orders` -> `sales__Orders` (schema `dbo` is dropped, any other schema is joined with a double underscore) |

Two servers with an `orders` table never collide: the server and database are in the dataset name,
and each target has its own bucket and control (checkpoint/audit) dataset.

## Access: why no per-project IAM
The tunnel/VPC is only for reading Azure SQL. Writes to GCS and BigQuery go over Google APIs and need IAM
only. Roles are granted once on a FOLDER; every project created inside inherits them. NCC / Shared VPC
are not needed.

## Setup, in order
1. Admin, once (Cloud Shell): `ORG_ID=<id> BILLING_ID=<id> bash scripts/gcp_multi_project_setup.sh`
   (find ids with `gcloud organizations list` and `gcloud billing accounts list`). It prints `folder_id`.
2. Edit `config/settings.yaml`: `multi_project.enabled: true`, `folder_id`, `billing_account`.
   No organization? leave `folder_id: ""` - the code then grants the runner roles per project.
3. Per target add `resource_group:` and `server:` (the 3 POC regions already have them).
4. Preview, creates nothing: `python3 main.py --list-projects`
5. Network for every SQL server (Azure private endpoint + Cloud DNS record) - same as the POC.
6. Run.

## Commands
```
python3 main.py --list-projects                                   # what maps where
python3 main.py --resource-group rg-na-eastus --check-network
python3 main.py --resource-group rg-na-eastus --all               # every server in the RG, every table
python3 main.py --resource-group rg-na-eastus --server sql-sts-eastus-vipqb --all
python3 main.py --resource-group rg-na-eastus --server sql-sts-eastus-vipqb --table dbo.orders
python3 main.py --region all --all                                # everything
```
Each target gets its project created on first use, then migrated. `--force`, `gcs`/`bq` stage work as before.

## Limits and notes
- Project ids are global; if the default id is taken, set `multi_project.projects.<rg>: <id>`. With an empty `project_prefix` the id is the resource group name, which must be 6-30 characters (lowercase letters, digits, hyphens, starting with a letter); a shorter or invalid name needs an entry in `multi_project.projects`.
- `multi_project.dataset_layout: per_schema` restores the earlier layout (`<server>_<database>_<schema>` datasets, plain table names). Choose before the first load: changing it later means reloading.
- Name collision to know about: `dbo.sales__Orders` and `sales.Orders` would both become `sales__Orders`. Rename one at the source if that ever happens. A table whose name ends in `_staging` would also clash with its own working table (as before).
- GCS and Blob paths are unchanged: `parquet/<schema>/<table>/`. Only BigQuery names changed.
- Billing accounts have a project quota; request an increase before ~100+ resource groups.
- New project access can take 1-2 minutes to appear; the code waits and retries.
- SQL logins are per target label: `AZURE_SQL_USERNAME_<LABEL>` / `AZURE_SQL_PASSWORD_<LABEL>` in `.env`.
- Views and stored procedures are NOT migrated by this change (tables only); that is the separate
  `--export-definitions` step.
- Not yet run against real GCP/Azure - verified offline with mocked `gcloud` (107 tests).
