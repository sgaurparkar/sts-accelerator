"""
migration_dag.py

Orchestrates the full-load pipeline for every table whose load_mode
auto-detects (or is overridden) to "full" — discovery -> keyset-paginated
extraction -> Parquet -> Azure upload -> STS transfer -> BigQuery
staging + MERGE -> checkpoint -> audit/logging.

Table discovery happens INSIDE a task (discover_full_load_tables), not
at DAG-parse time — parse time runs on every scheduler heartbeat and
should never depend on a live SQL Server connection. The list of
tables it returns is fanned out with Airflow's dynamic task mapping
(.expand()), so adding/removing a table in the source database changes
what this DAG does on its next run with zero code or config change.
Requires Airflow 2.3+ (dynamic task mapping) — Cloud Composer 2 images
ship this.

Deploy into Cloud Composer:
  gcloud composer environments storage dags import \\
    --environment <your-composer-env> --location <region> \\
    --source dags/migration_dag.py
(and make sure the whole `src/` and `config/` folders are also
uploaded alongside it, since this file imports from them.)
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
    dag_id="migration_full_load",
    default_args=default_args,
    schedule_interval=None,  # triggered manually / by an upstream signal
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["migration", "accelerator", "full-load"],
) as dag:

    @task
    def discover_full_load_tables() -> list[dict]:
        from src.planner.table_planner import build_table_plan
        settings = _load_yaml("settings.yaml")
        return [t for t in build_table_plan(settings) if t["load_mode"] == "full"]

    @task
    def migrate_table(table_cfg: dict, **context) -> dict:
        from src.pipeline.table_pipeline import run_table
        settings = _load_yaml("settings.yaml")
        logging_config = _load_yaml("logging.yaml")
        run_id = context["dag_run"].run_id
        # Trigger with conf {"force": true} to reload tables that already COMPLETED.
        force = bool((context["dag_run"].conf or {}).get("force", False))
        return run_table(settings, logging_config, table_cfg, run_id, force=force)

    @task(trigger_rule="all_done")  # generate the report even if some tables failed
    def generate_report(_upstream_results) -> dict:
        from src.reporting.report_generator import ReportGenerator
        settings = _load_yaml("settings.yaml")
        logging_config = _load_yaml("logging.yaml")
        return ReportGenerator(settings, logging_config).generate()

    migrate_results = migrate_table.expand(table_cfg=discover_full_load_tables())
    generate_report(migrate_results)