"""
dag_tasks.py — Airflow task bodies, free of Airflow imports (unit-testable).

DAG trigger conf uses the same keys as a run-spec job (see src/selection/run_spec.py):

  {"databases": ["SalesDB"], "tables": ["dbo.Orders", "dbo.Customers"], "mode": "full"}
  {"servers": ["sqlserver2"], "all_tables": true, "mode": "incremental"}
  {"databases": ["FinanceDB"], "tables": ["dbo.Transactions"], "mode": "partial",
   "date_column": "TxnDate", "from": "2026-01-01", "to": "2026-04-01", "replace_range": true}
  {"spec": "config/run_spec.yaml"}          # several jobs, path relative to the project root
  {"databases": ["SalesDB"], "objects": ["views", "procedures"]}            # raw objects only
  {"databases": ["SalesDB"], "all_tables": true, "objects": ["views"]}      # tables + raw views

A conf with `objects` but no tables/all_tables exports objects only (no tables).

XCom carries only table identities; each mapped task re-plans its table from
the live catalog (so the plan is never stale and XCom stays small).
"""
import os

from src.orchestration.runner import (
    discover,
    generate_reports,
    load_configs,
    plan_job,
    plan_object_job,
    project_root,
    provision,
    run_object_item,
    run_work_item,
    targets_for,
)
from src.selection.run_spec import job_from_mapping, load_spec
from src.topology.models import MigrationTarget

_SCOPE_KEYS = ("resource_groups", "servers", "databases", "azure_locations")


def jobs_from_conf(conf: dict, default_mode: str = "auto", default_all: bool = True, only_modes=None) -> list:
    conf = dict(conf or {})
    if conf.get("spec"):
        path = conf["spec"] if os.path.isabs(conf["spec"]) else os.path.join(project_root(), conf["spec"])
        return load_spec(path)
    conf.setdefault("mode", default_mode)
    objects_only = bool(conf.get("objects") or conf.get("object_types")) and not conf.get("all_tables")
    if not conf.get("tables") and not conf.get("tables_file") and default_all and not objects_only:
        conf.setdefault("all_tables", True)
    if only_modes and not conf.get("only_modes"):
        conf["only_modes"] = list(only_modes)
    conf.setdefault("name", "dag")
    return [job_from_mapping(conf)]


def discover_and_provision(conf: dict) -> list[dict]:
    settings, logging_config = load_configs()
    jobs = jobs_from_conf(conf)
    targets, problems = discover(settings)
    targets = [t for t in targets if any(t in targets_for(targets, j) for j in jobs)]
    if not targets:
        raise RuntimeError(f"No Azure SQL databases in scope (mapping problems: {problems}).")
    status = provision(settings, targets, logging_config)
    ready = [t for t in targets if status.get(t.key) == "OK"]
    failed = {k: v for k, v in status.items() if v != "OK"}
    if failed:
        print(f"[dag] {len(failed)} database(s) failed provisioning and are skipped: {failed}")
    if not ready:
        raise RuntimeError(f"Every database target failed provisioning: {failed}")
    return [t.to_dict() for t in ready]


def plan_tables(targets: list[dict], conf: dict, default_mode: str = "auto", only_modes=None) -> list[dict]:
    settings, _ = load_configs()
    target_objs = [MigrationTarget.from_dict(t) for t in targets]
    items, cache = [], {}
    for job in jobs_from_conf(conf, default_mode, only_modes=only_modes):
        plan = plan_job(settings, target_objs, job, slim=True, catalog_cache=cache)
        for label, reason in plan.skipped:
            print(f"[dag] SKIP {label}: {reason}")
        for key, err in plan.failures:
            print(f"[dag] PLAN FAILED {key}: {err}")
        for pat in plan.unmatched:
            print(f"[dag] NOT FOUND: table pattern '{pat}'")
        items.extend(plan.items)
    print(f"[dag] {len(items)} table(s) planned")
    return items


def migrate(item: dict, run_id: str) -> dict:
    settings, logging_config = load_configs()
    result = run_work_item(settings, logging_config, item, run_id)
    if result.get("status") == "FAILED":
        raise RuntimeError(result.get("error"))
    return result


def report(targets: list[dict]) -> list[dict]:
    settings, logging_config = load_configs()
    return generate_reports(settings, logging_config, [MigrationTarget.from_dict(t) for t in targets])


def plan_objects(targets: list[dict], conf: dict) -> list[dict]:
    """One slim item per database whose raw views / procedures the conf selects."""
    settings, _ = load_configs()
    target_objs = [MigrationTarget.from_dict(t) for t in targets]
    items = []
    for job in jobs_from_conf(conf):
        if not job.has_objects:
            continue
        plan = plan_object_job(settings, target_objs, job, slim=True)
        for key, err in plan.failures:
            print(f"[dag] OBJECT PLAN FAILED {key}: {err}")
        for pat in plan.unmatched:
            print(f"[dag] NOT FOUND: object pattern '{pat}'")
        items.extend(plan.items)
    print(f"[dag] {len(items)} database(s) with raw objects planned")
    return items


def migrate_objects(item: dict, run_id: str) -> dict:
    settings, logging_config = load_configs()
    result = run_object_item(settings, logging_config, item, run_id)
    if result.get("status") == "FAILED":
        raise RuntimeError(result.get("error"))
    return result
