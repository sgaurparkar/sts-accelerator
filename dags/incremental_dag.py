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
    dag_id="migration_incremental",
    default_args=default_args,
    schedule_interval="@daily",  # adjust to how often new rows should sync
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["migration", "accelerator", "incremental"],
) as dag:

    @task
    def discover_incremental_tables(**context) -> list[dict]:
        # The schedule has no conf, so by default EVERY configured region is synced;
        # trigger manually with {"region": "East US"} (or set SOURCE_REGION) to run one.
        return _plan_by_region(_regions(context, default="all"), "incremental")

    @task
    def sync_table(item: dict, **context) -> dict:
        from src.pipeline.table_pipeline import run_table
        settings = _settings_for_region(item["region"])
        logging_config = _load_yaml("logging.yaml")
        run_id = context["dag_run"].run_id
        if item["region"]:
            run_id = f"{run_id}:{item['region']}"
        # Trigger with conf {"region": "East US", "force": true} (force reloads tables that already COMPLETED).
        force = bool(((context["dag_run"].conf) or {}).get("force", False))
        return run_table(settings, logging_config, item["table_cfg"], run_id, force=force)

    sync_table.expand(item=discover_incremental_tables())
