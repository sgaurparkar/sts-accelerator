# Table inventory: type only the table name

`python3 main.py --table customer` now finds the SQL server for you. `--region` still works
and always wins when you give it.

## Commands

| You type | What happens |
|---|---|
| `python3 main.py --refresh-inventory` | Connects (through the tunnel) to every configured SQL server, lists all tables, saves `config/table_inventory.json` and mirrors it to BigQuery `<project>.migration_inventory.table_inventory`. |
| `python3 main.py --refresh-inventory --region "East US"` | Rescans just that server; other entries are kept. |
| `python3 main.py --show-inventory` | Tables per server, and how old the inventory is. |
| `python3 main.py --find-table customer` | Shows which server(s) hold it. Migrates nothing. |
| `python3 main.py --table customer` | Looks the table up, picks the server, migrates it. |
| `python3 main.py --table dbo.customer` | Same, restricted to schema `dbo`. |
| `python3 main.py --region "Germany West Central" --table customer` | Forces that server; the inventory is not used. |
| `python3 main.py --region all --table customer` | Runs on every server that has the table (and only those). |
| `python3 main.py --table customer --cutoff-date 2026-09-22` | Works the same as before. |

`--all`, `--list`, `--report` and `--check-network` still need `--region`.

## Flow for `--table customer`

```
inventory file ──hit──► region found ──► resolve server ──► extract ──► Parquet ──► GCS ──► BigQuery
      │
      └─miss──► scan all servers once ──► save inventory ──► look up again ──► (same as above)
                                              └─ still missing ──► clear error
```

## Rules the code follows

* Table names match case-insensitively; the spelling stored in the inventory is what is used.
* **Same table on several servers:** without `--region` it stops and lists them. Choose one with
  `--region "<name>"`, or use `--region all` to migrate from all of them.
* **A server that cannot be reached** during a scan keeps its previous entries and is marked
  `ERROR`. If a table is "not found" while a server could not be scanned, the message names that server.
* **Stale inventory:** it only warns after `inventory.max_age_hours` (default 168 h). A table that
  was added later is picked up by the automatic rescan on a miss. A table that was dropped or moved
  fails with the normal "table not found" error: run `--refresh-inventory`.
* The BigQuery copy is best effort. If it fails, a warning is printed and the run continues; the
  JSON file is what `--table` reads.
* Servers whose `host` is still a `<placeholder>` are skipped.

## Settings (`inventory:` in `config/settings.yaml`)

```yaml
inventory:
  file: config/table_inventory.json
  auto_refresh_on_miss: true
  max_age_hours: 168
  parallel_servers: 4          # servers scanned at once
  bigquery_sync: true
  bq_dataset: migration_inventory
  bq_location: US
```

The runner's service account needs `bigquery.datasets.create` and `bigquery.tables.create`
for the mirror (or set `bigquery_sync: false`).

## Limits

* Not tested against real SQL Servers; the logic is covered by offline tests (74 tests pass).
* `--table` still plans every table on the chosen server before picking yours (existing behaviour),
  which is slow on servers with thousands of tables.
* On a multi-runner setup, give each runner a copy of the inventory file (or run
  `--refresh-inventory` on each).
