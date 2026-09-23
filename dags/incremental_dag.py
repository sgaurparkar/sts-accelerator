"""
incremental_dag.py

Same shape as migration_dag.py, but for every table overridden to
load_mode: incremental in config/tables_override.yaml — extracts rows
in keyset-paginated batches, loads each batch to staging, then MERGEs
it into the target on primary key alone (insert-only — see
src/bigquery/bq_merge.py; there is no watermark column anywhere in
this pipeline, since the source data is static). Intended to run on a
schedule (e.g. daily) to periodically pick up newly appended rows on
tables you've marked "incremental", once the initial full historical
load (migration_dag.py) is done for them.

Table discovery happens inside discover_incremental_tables (not at
DAG-parse time) and is fanned out with dynamic task mapping — see the
comment in migration_dag.py for why. Requires Airflow 2.3+.
"""

import os
import sys
from datetime import datetime, timedelta

import yaml
from airflow import DAG
from airflow.decorators import task

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "..", "config")


def _load_yaml(name: str) -> dict:
    with open(os.path.join(CONFIG_DIR, name)) as f:
        return yaml.safe_load(f)


default_args = {"owner": "migration-accelerator", "retries": 2, "retry_delay": timedelta(minutes=5)}

with DAG(
    dag_id="migration_incremental",
    default_args=default_args,
    schedule_interval="@daily",  # adjust to how often new rows should sync
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["migration", "accelerator", "incremental"],
) as dag:

    @task
    def discover_incremental_tables() -> list[dict]:
        from src.planner.table_planner import build_table_plan
        settings = _load_yaml("settings.yaml")
        return [t for t in build_table_plan(settings) if t["load_mode"] == "incremental"]

    @task
    def sync_table(table_cfg: dict, **context) -> dict:
        from src.pipeline.table_pipeline import run_table
        settings = _load_yaml("settings.yaml")
        logging_config = _load_yaml("logging.yaml")
        run_id = context["dag_run"].run_id
        return run_table(settings, logging_config, table_cfg, run_id)

    sync_table.expand(table_cfg=discover_incremental_tables())
