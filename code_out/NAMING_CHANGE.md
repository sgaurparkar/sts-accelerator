# Naming change: the agreed hierarchy (multi-project mode)

| Azure | Google Cloud | Rule |
|---|---|---|
| Resource group | **Project** | named after the resource group (`rg-na-eastus` -> `rg-na-eastus`) |
| SQL server + database | **Dataset** | `<server>_<database>`, lower case (`sql_server_01_salesdb`) |
| Schema + table | **Table** | `dbo.Orders` -> `Orders`; `sales.Orders` -> `sales__Orders` |

Only the project is created per resource group by `main.py`; the bucket, dataset and tables are created
automatically on the first run.

## What changed in the code
| File | Change |
|---|---|
| `src/bigquery/dataset_naming.py` | new `bq_table_name()`; `dataset_for_schema(..., flat=True)` returns the one `<server>_<database>` dataset |
| `src/config/region_resolver.py` | `project_prefix: ""` gives project = resource group name; new `flat_naming()` and `multi_project.dataset_layout`; `source.flat_naming` is set on resolved settings |
| `src/bigquery/bq_merge.py` | target and staging tables, MERGE and synthetic-key ALTERs use the new names |
| `src/bigquery/bq_control_tables.py` | target and staging tables are created with the new names |
| `src/reporting/report_generator.py` | live row count looks in the new dataset/table |
| `main.py` | messages show the new names |
| `config/settings.yaml` | `project_prefix: ""`, `dataset_layout: server_db` |
| `tests/test_naming_hierarchy.py` | 10 new tests (117 tests pass in total) |

Not changed: GCS and Blob paths (`parquet/<schema>/<table>/`), checkpoints and audit rows (still keyed by source schema + table),
the control dataset (`migrated_data_<region>`), everything that reads SQL Server.

## Switching back
`multi_project.dataset_layout: per_schema` restores `<server>_<database>_<schema>` datasets with plain table names.
Single-project mode (`multi_project.enabled: false`) is unchanged.

## Before the first production load
Names are fixed by the first load. Data already loaded under the old names stays there; the new run creates new
datasets/tables and does not move the old ones. For the POC, delete the old datasets/projects, or reload.
