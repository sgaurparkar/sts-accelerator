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


def _fail(message: str):
    """Fail the task immediately (no retries) — a bad/missing region won't fix itself."""
    try:
        from airflow.exceptions import AirflowFailException
    except ImportError:  # very old Airflow
        raise RuntimeError(message)
    raise AirflowFailException(message)


def _requested_region(context, default=None):
    """Region from the DAG-run conf ({"region": "East US"} or "all"), else the
    SOURCE_REGION env var, else `default`."""
    from src.config.region_resolver import clean_requested, region_from_env
    conf = (context.get("dag_run") and context["dag_run"].conf) or {}
    return clean_requested(conf.get("region")) or region_from_env() or default


def _regions(context, default=None) -> list:
    from src.config.region_resolver import RegionError, regions_to_run
    try:
        return regions_to_run(_load_yaml("settings.yaml"), _requested_region(context, default))
    except RegionError as e:
        _fail(str(e))


def _settings_for_region(region) -> dict:
    """settings.yaml pointed at one regional SQL server / GCP region (region=None
    in legacy single-server mode)."""
    from src.config.region_resolver import RegionError, resolve_region
    try:
        return resolve_region(_load_yaml("settings.yaml"), region)
    except RegionError as e:
        _fail(str(e))


def _plan_by_region(regions, load_mode: str) -> list[dict]:
    """[{"region": ..., "table_cfg": ...}, ...] for every table of `load_mode` in every
    region. One unreachable region is reported loudly but doesn't stop the others;
    if EVERY region fails the task fails."""
    from src.planner.table_planner import build_table_plan
    items, errors = [], []
    for region in regions:
        try:
            plan = build_table_plan(_settings_for_region(region))
            items += [{"region": region, "table_cfg": t} for t in plan if t["load_mode"] == load_mode]
        except Exception as exc:
            print(f"DISCOVERY FAILED | Region={region} | Reason={exc}")
            errors.append(f"{region}: {exc}")
    if errors and len(errors) == len(regions):
        _fail("Table discovery failed for every region — " + " | ".join(errors))
    return items


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
    def discover_full_load_tables(**context) -> list[dict]:
        # trigger with conf {"region": "East US"} (or "all"); required when `regions:` is configured
        return _plan_by_region(_regions(context), "full")

    @task
    def migrate_table(item: dict, **context) -> dict:
        from src.pipeline.table_pipeline import run_table
        settings = _settings_for_region(item["region"])
        logging_config = _load_yaml("logging.yaml")
        # one run_id per region+table, so checkpoints/audit rows never collide
        run_id = context["dag_run"].run_id
        if item["region"]:
            run_id = f"{run_id}:{item['region']}"
        # Trigger with conf {"region": "East US", "force": true} (force reloads tables that already COMPLETED).
        force = bool(((context["dag_run"].conf) or {}).get("force", False))
        return run_table(settings, logging_config, item["table_cfg"], run_id, force=force)

    @task(trigger_rule="all_done")  # generate the report even if some tables failed
    def generate_report(_upstream_results, **context) -> list:
        from src.reporting.report_generator import ReportGenerator
        logging_config = _load_yaml("logging.yaml")
        return [ReportGenerator(_settings_for_region(r), logging_config).generate()
                for r in _regions(context)]

    migrate_results = migrate_table.expand(item=discover_full_load_tables())
    generate_report(migrate_results)
