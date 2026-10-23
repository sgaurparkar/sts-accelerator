"""
incremental_dag.py — scheduled incremental sync (MIGRATION_INCREMENTAL_SCHEDULE, default hourly).

By default it syncs the tables marked `load_mode: incremental` in
config/tables_override.yaml (optionally scoped to resource_group / server / database).
Set MIGRATION_INCREMENTAL_ALL=1 to run every table in scope incrementally
(tables without a key or watermark are skipped and listed in the logs).
Trigger conf narrows it like the on-demand DAG, e.g. {"databases": ["SalesDB"]}.

Each run reads only rows changed since the previous successful run (rowversion,
an updated-at column, or an increasing key) and upserts them by key.
"""
import os
import sys
import uuid

import pendulum
from airflow.decorators import dag, task

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.orchestration import dag_tasks  # noqa: E402

_ALL = os.environ.get("MIGRATION_INCREMENTAL_ALL", "0") == "1"


@dag(
    dag_id="migration_incremental",
    schedule=os.environ.get("MIGRATION_INCREMENTAL_SCHEDULE", "@hourly"),
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=int(os.environ.get("MIGRATION_MAX_PARALLEL_TABLES", "8")),
    tags=["migration", "azure-sql", "bigquery", "incremental"],
    doc_md=__doc__,
)
def migration_incremental():
    def _conf(context) -> dict:
        conf = dict((context.get("dag_run").conf if context.get("dag_run") else None) or {})
        if _ALL:
            conf.setdefault("mode", "incremental")
        return conf

    @task
    def discover_and_provision(**context) -> list[dict]:
        return dag_tasks.discover_and_provision(_conf(context))

    @task
    def plan_tables(targets: list[dict], **context) -> list[dict]:
        conf = _conf(context)
        if _ALL or conf.get("mode") == "incremental":
            return dag_tasks.plan_tables(targets, conf, default_mode="incremental")
        return dag_tasks.plan_tables(targets, conf, default_mode="auto", only_modes=["incremental"])

    @task(retries=2, retry_delay=pendulum.duration(minutes=5))
    def migrate_table(item: dict, **context) -> dict:
        run_id = context["dag_run"].run_id if context.get("dag_run") else str(uuid.uuid4())
        return dag_tasks.migrate(item, run_id)

    @task(trigger_rule="all_done")
    def report(targets: list[dict]) -> list[dict]:
        return dag_tasks.report(targets)

    targets = discover_and_provision()
    items = plan_tables(targets)
    migrate_table.expand(item=items) >> report(targets)


migration_incremental()
