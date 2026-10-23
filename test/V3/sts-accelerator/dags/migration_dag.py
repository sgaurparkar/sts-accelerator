"""
migration_dag.py — on-demand migration (Cloud Composer / Airflow 2.4+).

    discover_and_provision -> plan_tables  -> migrate_table.expand()  -> report
                           -> plan_objects -> migrate_objects.expand()   (raw views / procedures)

Trigger conf = one run-spec job (all keys optional; default: every table, mode auto):

    {"databases": ["SalesDB"], "tables": ["dbo.Orders"], "mode": "full", "force": true}
    {"resource_groups": ["ResourceGroup1"], "all_tables": true, "mode": "full"}
    {"databases": ["HRDB"], "tables": ["dbo.*"], "exclude_tables": ["dbo.tmp_*"], "mode": "incremental"}
    {"databases": ["FinanceDB"], "tables": ["dbo.Transactions"], "mode": "partial",
     "date_column": "TxnDate", "from": "2026-01-01", "to": "2026-04-01", "replace_range": true}
    {"spec": "config/run_spec.yaml"}
    {"databases": ["SalesDB"], "objects": ["views", "procedures"]}          raw objects only
    {"databases": ["SalesDB"], "all_tables": true, "objects": ["all"]}     tables + every object type

Notes
  * Airflow maps at most `max_map_length` tasks (default 1024): raise it in the Composer
    Airflow config ([core] max_map_length) if one run selects more tables.
  * MIGRATION_MAX_PARALLEL_TABLES caps concurrent tables; use an Airflow pool per SQL
    server if small databases must not see more than one or two readers.
  * A per-table lock in BigQuery stops this DAG and the incremental DAG from working on
    the same table at the same time.

Deploy: upload dags/, src/ and config/ side by side to the Composer DAG bucket, or set
STS_ACCELERATOR_HOME to the folder holding config/settings.yaml.
"""
import os
import sys
import uuid

import pendulum
from airflow.decorators import dag, task

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.orchestration import dag_tasks  # noqa: E402


@dag(
    dag_id="migration_run",
    schedule=None,
    start_date=pendulum.datetime(2026, 1, 1, tz="UTC"),
    catchup=False,
    max_active_runs=1,
    max_active_tasks=int(os.environ.get("MIGRATION_MAX_PARALLEL_TABLES", "8")),
    tags=["migration", "azure-sql", "bigquery"],
    doc_md=__doc__,
)
def migration_run():
    @task
    def discover_and_provision(**context) -> list[dict]:
        return dag_tasks.discover_and_provision(context["dag_run"].conf or {})

    @task
    def plan_tables(targets: list[dict], **context) -> list[dict]:
        return dag_tasks.plan_tables(targets, context["dag_run"].conf or {})

    @task(retries=2, retry_delay=pendulum.duration(minutes=10))
    def migrate_table(item: dict, **context) -> dict:
        return dag_tasks.migrate(item, context["dag_run"].run_id or str(uuid.uuid4()))

    @task
    def plan_objects(targets: list[dict], **context) -> list[dict]:
        return dag_tasks.plan_objects(targets, context["dag_run"].conf or {})

    @task(retries=2, retry_delay=pendulum.duration(minutes=5))
    def migrate_objects(item: dict, **context) -> dict:
        return dag_tasks.migrate_objects(item, context["dag_run"].run_id or str(uuid.uuid4()))

    @task(trigger_rule="all_done")
    def report(targets: list[dict]) -> list[dict]:
        return dag_tasks.report(targets)

    targets = discover_and_provision()
    items = plan_tables(targets)
    object_items = plan_objects(targets)
    done_report = report(targets)
    migrate_table.expand(item=items) >> done_report
    migrate_objects.expand(item=object_items) >> done_report


migration_run()
