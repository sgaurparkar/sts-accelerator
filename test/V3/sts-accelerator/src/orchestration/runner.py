"""
runner.py — the orchestration steps shared by main.py and the Airflow DAGs.

  load_configs   settings.yaml + logging.yaml (+ secrets provider, logging)
  discover       every Azure SQL database in settings scope -> MigrationTargets
  targets_for    the targets one job touches (its resource group / server / database /
                 location filters)
  provision      one GCP project per resource group + bucket / datasets / control tables
  plan_job       every (database, table) the job selects, planned for its load mode
  run_items      migrate them — in parallel (--parallel), never more than
                 --max-per-server at once against one SQL server
  plan_object_job / run_object_items
                 raw views / stored procedures (src/objects): one work item per database
  generate_reports
"""
import os
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field

import yaml

from src.bigquery.bq_control_tables import BqControlTables
from src.catalog.catalog_reader import CatalogReader
from src.common import log
from src.config.connection import configure_secrets
from src.gcp.project_provisioner import ProjectProvisioner
from src.gcs.gcs_manager import GcsManager
from src.objects.object_catalog import ObjectCatalogReader
from src.objects.object_pipeline import is_full_scope, object_selector, plan_objects, run_objects
from src.pipeline.table_pipeline import run_table
from src.planner.table_planner import plan_database, plan_one
from src.reporting.report_generator import ReportGenerator
from src.selection.run_spec import Job
from src.selection.table_selector import TableSelector
from src.topology.azure_discovery import filter_databases
from src.topology.models import MigrationTarget
from src.topology.targets import build_migration_targets, projects_of, settings_for_target

LOG = log.get("runner")


def project_root() -> str:
    """STS_ACCELERATOR_HOME, else the folder holding config/settings.yaml."""
    env = os.environ.get("STS_ACCELERATOR_HOME")
    if env:
        return env
    here = os.path.dirname(os.path.abspath(__file__))
    for candidate in (os.getcwd(), os.path.dirname(os.path.dirname(here)),
                      os.path.dirname(os.path.dirname(os.path.dirname(here)))):
        if os.path.exists(os.path.join(candidate, "config", "settings.yaml")):
            return candidate
    return os.getcwd()


def load_yaml(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise SystemExit(f"Could not parse {path}: {e}")
    if not isinstance(data, dict):
        raise SystemExit(f"{path} is empty or not a mapping.")
    return data


def load_configs(root: str | None = None) -> tuple[dict, dict]:
    root = root or project_root()
    settings = load_yaml(os.path.join(root, "config", "settings.yaml"))
    logging_config = load_yaml(os.path.join(root, "config", "logging.yaml"))
    log.setup(logging_config.get("level"))
    for key in ("overrides_file", "plans_dir"):
        if settings.get(key) and not os.path.isabs(settings[key]):
            settings[key] = os.path.join(root, settings[key])
    extraction = settings.setdefault("extraction", {})
    if extraction.get("local_dir") and not os.path.isabs(extraction["local_dir"]):
        extraction["local_dir"] = os.path.join(root, extraction["local_dir"])
    export = (settings.get("objects") or {}).get("export") or {}
    if export.get("local_dir") and not os.path.isabs(export["local_dir"]):
        export["local_dir"] = os.path.join(root, export["local_dir"])
    if logging_config.get("report_output_path") and not os.path.isabs(logging_config["report_output_path"]):
        logging_config["report_output_path"] = os.path.join(root, logging_config["report_output_path"])
    configure_secrets(settings.get("secrets"))
    return settings, logging_config


# ------------------------------------------------------------------ targets --
def discover(settings: dict, filters: dict | None = None, strict: bool = False) -> tuple[list, list]:
    problems: list[str] = []
    targets = build_migration_targets(settings, filters, strict=strict, problems_out=problems)
    return targets, problems


def targets_for(targets: list[MigrationTarget], job: Job) -> list[MigrationTarget]:
    keep = {d.key for d in filter_databases([t.source for t in targets], job.filters())}
    return [t for t in targets if t.key in keep]


def provision(settings: dict, targets: list[MigrationTarget], logging_config: dict | None = None,
              provisioner: ProjectProvisioner | None = None) -> dict:
    """{target.key: "OK" | "<error>"}; a failing project fails only its databases."""
    provisioner = provisioner or ProjectProvisioner(settings)
    project_errors: dict[str, str] = {}
    for project_id, t in projects_of(targets).items():
        try:
            provisioner.ensure_project(project_id, t.gcp_project_display_name, t.source.resource_group)
        except Exception as e:
            LOG.error("Project %s could not be provisioned: %s", project_id, e)
            project_errors[project_id] = str(e)
    status = {}
    for t in targets:
        if t.gcp_project_id in project_errors:
            status[t.key] = f"project {t.gcp_project_id}: {project_errors[t.gcp_project_id]}"
            continue
        try:
            ts = settings_for_target(settings, t)
            GcsManager(ts).ensure_bucket()
            BqControlTables(ts).ensure_all()
            status[t.key] = "OK"
        except Exception as e:
            LOG.error("Could not prepare %s: %s", t.describe(), e)
            status[t.key] = str(e)
    return status


# ----------------------------------------------------------------- planning --
@dataclass
class PlanResult:
    items: list = field(default_factory=list)          # runnable work items
    skipped: list = field(default_factory=list)        # (label, reason)
    failures: list = field(default_factory=list)       # (database key, error)
    unmatched: list = field(default_factory=list)      # exact table patterns that matched nothing


def item_label(item: dict) -> str:
    t = item["target"]["source"]
    return f"{t['resource_group']}/{t['server']}/{t['database']}:{item['schema']}.{item['name']}"


def plan_job(settings: dict, targets: list[MigrationTarget], job: Job, slim: bool = False,
             catalog_cache: dict | None = None, reader_factory=CatalogReader) -> PlanResult:
    result = PlanResult()
    if not job.has_tables:                       # an objects-only job
        return result
    selector = TableSelector(job.tables, job.exclude_tables, job.schemas)
    cache = catalog_cache if catalog_cache is not None else {}
    for t in targets_for(targets, job):
        s = t.source
        if not job.all_tables and not selector.applies_to_database(s.resource_group, s.server, s.database):
            continue
        try:
            ts = settings_for_target(settings, t)
            if t.key not in cache:
                schemas = ts["source"].get("schemas") or None
                cache[t.key] = reader_factory(ts).read(schemas=schemas if schemas not in ("*", ["*"]) else None)
            plans = plan_database(ts, job, selector=selector, catalog=cache[t.key])
        except Exception as e:
            LOG.error("Planning failed for %s: %s", t.key, e)
            result.failures.append((t.key, str(e)))
            continue
        for cfg in plans:
            item = {"job": job.to_dict(), "target": t.to_dict(), "schema": cfg["schema"], "name": cfg["name"],
                    "mode": cfg["mode"], "row_estimate": cfg.get("row_estimate"),
                    "table": None if slim else cfg}
            if cfg.get("skip_reason"):
                result.skipped.append((item_label(item), cfg["skip_reason"]))
            else:
                result.items.append(item)
    result.unmatched = selector.unmatched_exact()
    result.items.sort(key=lambda i: -(i.get("row_estimate") or 0))
    return result


# ---------------------------------------------------------------- execution --
def run_work_item(settings: dict, logging_config: dict, item: dict, batch_run_id: str | None = None,
                  ignore_locks: bool = False) -> dict:
    target = MigrationTarget.from_dict(item["target"])
    job = Job.from_dict(item["job"])
    ts = settings_for_target(settings, target)
    cfg = item.get("table") or plan_one(ts, job, item["schema"], item["name"])
    label = item_label(item)
    if cfg is None:
        return {"table": label, "status": "SKIPPED", "reason": "not part of this job", "rows": 0}
    run_id = f"{batch_run_id or uuid.uuid4()}:{label}"
    return run_table(ts, logging_config, cfg, run_id, stage=job.stage, force=job.force, ignore_locks=ignore_locks)


def run_items(settings: dict, logging_config: dict, items: list[dict], batch_run_id: str,
              parallel: int = 1, max_per_server: int = 2, ignore_locks: bool = False) -> list[dict]:
    """Runs every item; one failing table never stops the others."""
    if not items:
        return []

    def work(item):
        try:
            return run_work_item(settings, logging_config, item, batch_run_id, ignore_locks)
        except Exception as e:
            return {"table": item_label(item), "status": "FAILED", "error": str(e), "rows": 0}

    return _run_parallel(items, work, parallel, max_per_server)


def _run_parallel(items: list[dict], work, parallel: int, max_per_server: int) -> list[dict]:
    """work(item) for every item, `parallel` at a time, at most `max_per_server`
    at once against one SQL server (item["target"]["source"]["server_fqdn"])."""
    slots: dict[str, threading.Semaphore] = {}
    guard = threading.Lock()

    def slot_for(item):
        server = item["target"]["source"]["server_fqdn"].lower()
        with guard:
            return slots.setdefault(server, threading.Semaphore(max(1, max_per_server)))

    def guarded(item):
        with slot_for(item):
            return work(item)

    if parallel <= 1:
        return [guarded(i) for i in items]
    results = []
    with ThreadPoolExecutor(max_workers=parallel, thread_name_prefix="work") as pool:
        futures = [pool.submit(guarded, i) for i in items]
        for f in as_completed(futures):
            results.append(f.result())
    return results


# ------------------------------------------------------- raw views / procedures --
@dataclass
class ObjectPlanResult:
    items: list = field(default_factory=list)          # one per database: {job, target, objects}
    failures: list = field(default_factory=list)       # (database key, error)
    unmatched: list = field(default_factory=list)      # exact object patterns that matched nothing


def plan_object_job(settings: dict, targets: list[MigrationTarget], job: Job, slim: bool = False,
                    reader_factory=ObjectCatalogReader) -> ObjectPlanResult:
    """Which objects of which databases the job exports (raw, untranslated)."""
    result = ObjectPlanResult()
    if not job.has_objects:
        return result
    selector = object_selector(job)
    for t in targets_for(targets, job):
        s = t.source
        if not selector.applies_to_database(s.resource_group, s.server, s.database):
            continue
        try:
            ts = settings_for_target(settings, t)
            objects = plan_objects(ts, job, selector=selector, reader=reader_factory(ts))
        except Exception as e:
            LOG.error("Object planning failed for %s: %s", t.key, e)
            result.failures.append((t.key, str(e)))
            continue
        if objects or is_full_scope(ts, job):
            result.items.append({"job": job.to_dict(), "target": t.to_dict(), "object_count": len(objects),
                                 "objects": None if slim else objects})
    result.unmatched = selector.unmatched_exact()
    result.items.sort(key=lambda i: -(i.get("object_count") or 0))
    return result


def object_item_label(item: dict) -> str:
    t = item["target"]["source"]
    return f"{t['resource_group']}/{t['server']}/{t['database']}"


def run_object_item(settings: dict, logging_config: dict, item: dict, batch_run_id: str | None = None,
                    ignore_locks: bool = False) -> dict:
    target = MigrationTarget.from_dict(item["target"])
    job = Job.from_dict(item["job"])
    ts = settings_for_target(settings, target)
    run_id = f"{batch_run_id or uuid.uuid4()}:{object_item_label(item)}:objects"
    return run_objects(ts, logging_config, job, run_id, objects=item.get("objects"), ignore_locks=ignore_locks)


def run_object_items(settings: dict, logging_config: dict, items: list[dict], batch_run_id: str,
                     parallel: int = 1, max_per_server: int = 2, ignore_locks: bool = False) -> list[dict]:
    if not items:
        return []

    def work(item):
        try:
            return run_object_item(settings, logging_config, item, batch_run_id, ignore_locks)
        except Exception as e:
            return {"database": object_item_label(item), "status": "FAILED", "error": str(e), "objects": 0}

    return _run_parallel(items, work, parallel, max_per_server)


def generate_reports(settings: dict, logging_config: dict, targets: list[MigrationTarget]) -> list[dict]:
    done, results = set(), []
    for t in targets:
        key = (t.gcp_project_id, t.control_dataset_id)
        if key in done:
            continue
        done.add(key)
        try:
            results.append(ReportGenerator(settings_for_target(settings, t), logging_config).generate())
        except Exception as e:
            LOG.warning("Report for %s.%s failed: %s", key[0], key[1], e)
    return results
