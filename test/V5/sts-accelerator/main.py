"""
main.py — Azure SQL -> BigQuery migration: tables (data) + raw views / stored procedures.

Azure Resource Group -> GCP project, SQL Server + Database -> BigQuery dataset,
Azure region -> GCP region, all discovered and derived at run time.

WHAT TO MIGRATE  (combine freely; patterns are case-insensitive, wildcards allowed)
  --all                                   every table of every database in scope
  --tables dbo.Orders                     one table (in every database in scope that has it)
  --tables dbo.Orders sales.Customers     several tables (or comma-separated)
  --tables "dbo.*" --exclude-tables "dbo.tmp_*"
  --tables SalesDB/dbo.Orders HRDB/dbo.Employees     tables of specific databases
  --tables-file tables.txt                one pattern per line
  --resource-group / --server / --database / --azure-location / --schema   narrow the databases/schemas
  --spec run.yaml                         several jobs with different scopes/modes in one run

RAW VIEWS / STORED PROCEDURES  (original T-SQL, NOT translated — see README "Raw objects")
  --objects                               views + procedures (settings objects.types)
  --objects views | procedures | functions | triggers | all
  --views / --procedures                  shortcuts
  --object-names "dbo.vw_*" SalesDB/dbo.usp_Load   --exclude-objects "dbo.tmp_*"
  (alone: objects only; together with --all / --tables: tables first, then objects)

HOW TO LOAD
  --mode auto (default) | full | incremental | partial
  --where "Region = 'IN'"                 partial: any SQL Server predicate
  --date-column OrderDate --from 2026-01-01 --to 2026-02-01 [--replace-range]
  --cutoff-date 2026-09-22 | none         full loads: only rows up to this date
  --force                                 full: reload COMPLETED tables; incremental: re-read all rows

ACTIONS
  --show-topology | --check-connections | --provision | --list | --report
  (migrate: --all / --tables / --objects / --spec)
  --dry-run                               plan and print, migrate nothing

EXECUTION
  --parallel 4 --max-per-server 2 --stage bq|gcs --skip-provision --strict --ignore-locks

Exit code: 0 everything succeeded/skipped, 1 something failed, 2 bad arguments.
"""
import argparse
import os
import sys
import uuid

from src.common import log
from src.objects.object_pipeline import objects_config
from src.objects.object_types import FOLDERS, ObjectTypeError
from src.orchestration.runner import (
    discover,
    generate_reports,
    load_configs,
    object_item_label,
    plan_job,
    plan_object_job,
    provision,
    run_items,
    run_object_items,
    targets_for,
)
from src.selection.run_spec import MODES, MODE_ALIASES, Job, RunSpecError, load_spec
from src.selection.table_selector import TablePatternError, expand_values, read_tables_file
from src.topology.azure_discovery import TopologyError
from src.topology.targets import format_topology

LOG = log.get("main")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="main.py", formatter_class=argparse.RawDescriptionHelpFormatter,
                                description=__doc__)
    act = p.add_argument_group("actions (default: migrate)")
    act.add_argument("--show-topology", action="store_true", help="print the Azure -> GCP mapping; changes nothing")
    act.add_argument("--check-connections", action="store_true",
                     help="per database: which .env variables are used, can we log in, can we read definitions")
    act.add_argument("--provision", action="store_true", help="create/verify projects, buckets, datasets only")
    act.add_argument("--list", action="store_true", help="plan and list the selected tables; migrates nothing")
    act.add_argument("--report", action="store_true", help="(re)generate the status report(s)")
    act.add_argument("--dry-run", action="store_true", help="with a migrate selection: plan and print only")

    sel = p.add_argument_group("what to migrate")
    sel.add_argument("--all", action="store_true", help="every table of every database in scope")
    sel.add_argument("--tables", "--table", nargs="+", metavar="PATTERN", dest="tables",
                     help="table patterns: Orders, dbo.Orders, dbo.*, SalesDB/dbo.Orders, srv/db/schema.table")
    sel.add_argument("--tables-file", metavar="FILE", help="file with one table pattern per line")
    sel.add_argument("--exclude-tables", nargs="+", metavar="PATTERN", default=[])
    sel.add_argument("--schema", "--schemas", nargs="+", dest="schemas", metavar="SCHEMA", default=[])
    sel.add_argument("--resource-group", nargs="+", metavar="RG", default=[])
    sel.add_argument("--server", nargs="+", metavar="SERVER", default=[])
    sel.add_argument("--database", nargs="+", metavar="DB", default=[])
    sel.add_argument("--azure-location", nargs="+", metavar="LOC", default=[])
    sel.add_argument("--spec", metavar="RUN_YAML", help="run spec with several jobs (see config/run_spec.example.yaml)")

    obj = p.add_argument_group("raw views / stored procedures (original T-SQL, not translated)")
    obj.add_argument("--objects", nargs="*", metavar="TYPE", default=None,
                     help="views, procedures, functions, triggers or all (no value = settings objects.types)")
    obj.add_argument("--views", action="store_true", help="same as --objects views")
    obj.add_argument("--procedures", "--procs", action="store_true", dest="procedures",
                     help="same as --objects procedures")
    obj.add_argument("--object-names", nargs="+", metavar="PATTERN", default=[],
                     help="object patterns, same syntax as --tables: dbo.vw_*, SalesDB/dbo.usp_Load")
    obj.add_argument("--exclude-objects", nargs="+", metavar="PATTERN", default=[])

    mode = p.add_argument_group("how to load")
    mode.add_argument("--mode", default="auto",
                      help=f"one of {', '.join(MODES)} (aliases: {', '.join(sorted(MODE_ALIASES))})")
    mode.add_argument("--where", help="partial: SQL Server predicate, e.g. \"Region = 'IN'\"")
    mode.add_argument("--date-column", help="partial: date/time column for --from/--to")
    mode.add_argument("--from", dest="date_from", help="partial: start (inclusive), YYYY-MM-DD[THH:MM:SS]")
    mode.add_argument("--to", dest="date_to", help="partial: end (exclusive)")
    mode.add_argument("--replace-range", action="store_true",
                      help="partial: delete the --from/--to range in BigQuery before loading it")
    mode.add_argument("--cutoff-date", help="full: only rows with extraction.cutoff_column up to this date; "
                                            "'none' disables the configured cutoff")
    mode.add_argument("--force", action="store_true",
                      help="full: reload even if COMPLETED; incremental: re-read every row")
    mode.add_argument("--incremental-only", action="store_true",
                      help="with --mode auto: only tables that load incrementally (key + watermark, or load_mode: incremental)")

    ex = p.add_argument_group("execution")
    ex.add_argument("--stage", choices=["bq", "gcs"], default="bq", help="gcs = stop once files are in GCS")
    ex.add_argument("--parallel", type=int, default=1, help="tables migrated at the same time (default 1)")
    ex.add_argument("--max-per-server", type=int, default=2,
                    help="at most this many tables at once from one SQL server (default 2)")
    ex.add_argument("--skip-provision", action="store_true", help="projects/buckets/datasets already exist")
    ex.add_argument("--strict", action="store_true",
                    help="fail if any database can't be mapped or any exact table pattern matches nothing")
    ex.add_argument("--ignore-locks", action="store_true", help="don't take per-table locks (single runner only)")
    ex.add_argument("--log-level", default=None, help="DEBUG, INFO (default), WARNING")
    return p


def _object_kinds(args) -> list[str]:
    kinds = list(expand_values(args.objects or []))
    if args.views:
        kinds.append("views")
    if args.procedures:
        kinds.append("procedures")
    if args.objects is not None and not kinds:
        kinds = list(getattr(args, "default_object_kinds", None) or ["views", "procedures"])
    return kinds


def _cli_job(args) -> Job:
    tables = expand_values(args.tables)
    if args.tables_file:
        tables += read_tables_file(args.tables_file)
    all_tables = bool(args.all) or tables in (["*"], ["*.*"])
    if all_tables and tables in (["*"], ["*.*"]):
        tables = []
    return Job(
        name="cli",
        resource_groups=expand_values(args.resource_group), servers=expand_values(args.server),
        databases=expand_values(args.database), azure_locations=expand_values(args.azure_location),
        all_tables=all_tables, tables=tables, exclude_tables=expand_values(args.exclude_tables),
        schemas=expand_values(args.schemas),
        only_modes=["incremental"] if args.incremental_only else [],
        mode=args.mode, where=args.where, date_column=args.date_column, date_from=args.date_from,
        date_to=args.date_to, replace_range=args.replace_range, cutoff_date=args.cutoff_date,
        force=args.force, stage=args.stage,
        objects=_object_kinds(args), object_names=expand_values(args.object_names),
        exclude_objects=expand_values(args.exclude_objects),
    ).validate()


def build_jobs(args) -> list[Job]:
    """One job from the flags, or the jobs of --spec (with --force/--stage applied on top)."""
    if args.spec:
        if args.tables or args.tables_file or args.all or args.where or args.date_column or _object_kinds(args):
            raise RunSpecError("--spec defines the selection itself — don't combine it with "
                               "--all/--tables/--objects/--where.")
        jobs = load_spec(args.spec)
        for j in jobs:
            if args.force:
                j.force = True
            if args.stage != "bq":
                j.stage = args.stage
            for name in ("resource_groups", "servers", "databases", "azure_locations"):
                cli_value = expand_values(getattr(args, name[:-1] if name != "azure_locations" else "azure_location"))
                if cli_value and not getattr(j, name):
                    setattr(j, name, cli_value)
        return jobs
    return [_cli_job(args)]


def _print_plan(job: Job, plan) -> None:
    print(f"\n### job '{job.name}': mode={job.mode}  ({len(plan.items)} table(s) to run, {len(plan.skipped)} skipped)")
    current = None
    for it in plan.items:
        db = "{resource_group}/{server}/{database}".format(**it["target"]["source"])
        if db != current:
            current = db
            print(f"  {db} -> {it['target']['gcp_project_id']}.{it['target']['dataset_id']}"
                  f"  [{it['target']['gcp_location']}]")
        t = it["table"] or {}
        wm = (t.get("watermark") or {}).get("column")
        key = ",".join((t.get("key") or {}).get("columns") or []) or "(none)"
        est = it.get("row_estimate")
        parts = [f"    {it['schema'] + '.' + it['name']:40s} -> {t.get('bq_table', ''):32s}",
                 f"mode={it['mode']:<11s}", f"strategy={t.get('strategy', '?'):<7s}", f"key={key}"]
        if wm:
            parts.append(f"watermark={wm}")
        if est is not None:
            parts.append(f"~{est:,} rows")
        if t.get("load_plan") and t.get("load_plan") != it["mode"]:
            parts.append(f"[{t['load_plan']}]")
        print("  ".join(parts))
    for label, reason in plan.skipped:
        print(f"    SKIP {label}: {reason}")
    for key, err in plan.failures:
        print(f"    PLAN FAILED {key}: {err}")
    for pat in plan.unmatched:
        print(f"    NOT FOUND table pattern '{pat}' matched no table in any selected database")


def _print_object_plan(job: Job, plan) -> None:
    kinds = ", ".join(FOLDERS.get(k, k) for k in job.objects)
    total = sum(i.get("object_count") or 0 for i in plan.items)
    print(f"\n### job '{job.name}': raw objects ({kinds}) — {total} object(s) in {len(plan.items)} database(s), "
          "original T-SQL, not translated")
    for it in plan.items:
        t = it["target"]
        print(f"  {object_item_label(it)} -> {t['gcp_project_id']}.{t['control_dataset_id']}.migration_sql_objects"
              f"  [{t['gcp_location']}]  ({it.get('object_count') or 0} object(s))")
        for o in sorted(it.get("objects") or [], key=lambda x: (x["kind"], x["schema"].lower(), x["name"].lower())):
            flag = "  (encrypted: no definition)" if o.get("is_encrypted") else ""
            parent = f"  on {o['parent_object']}" if o.get("parent_object") else ""
            print(f"    {o['kind']:<10s} {o['schema'] + '.' + o['name']:60s} modified {o.get('modified_at') or '?'}"
                  f"{parent}{flag}")
    for key, err in plan.failures:
        print(f"    PLAN FAILED {key}: {err}")
    for pat in plan.unmatched:
        print(f"    NOT FOUND object pattern '{pat}' matched no {kinds} in any selected database")


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.log_level:
        os.environ["STS_LOG_LEVEL"] = args.log_level

    actions = [n for n in ("show_topology", "check_connections", "provision", "report", "list") if getattr(args, n)]
    wants_objects = args.objects is not None or args.views or args.procedures
    has_selection = bool(args.all or args.tables or args.tables_file or args.spec or wants_objects)
    if len(actions) > 1:
        parser.error("choose only one of --show-topology, --check-connections, --provision, --report, --list")
    if (args.object_names or args.exclude_objects) and not wants_objects and not args.spec:
        parser.error("--object-names / --exclude-objects need --objects (or --views / --procedures)")
    if args.list and not has_selection:
        args.all = has_selection = True
    if args.dry_run and not has_selection:
        parser.error("--dry-run needs a selection (--all, --tables, --tables-file or --spec)")
    if not actions and not has_selection:
        parser.error("nothing to do — pass --all, --tables ..., --tables-file, --spec, or an action like --list")
    migrating = has_selection and not actions and not args.dry_run

    settings, logging_config = load_configs()
    try:
        args.default_object_kinds = objects_config(settings)["types"]
    except ValueError as e:
        parser.exit(2, f"error: settings.yaml objects.types: {e}\n")
    try:
        jobs = build_jobs(args) if has_selection and not (actions and actions[0] != "list") else []
    except (RunSpecError, TablePatternError, ObjectTypeError, OSError) as e:
        parser.exit(2, f"error: {e}\n")

    scope = {"resource_groups": expand_values(args.resource_group), "servers": expand_values(args.server),
             "databases": expand_values(args.database), "azure_locations": expand_values(args.azure_location)}
    try:
        targets, problems = discover(settings, scope if not args.spec else None, strict=args.strict)
    except TopologyError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    if not targets:
        print("No Azure SQL databases in scope — check azure_topology in config/settings.yaml and the filters.",
              file=sys.stderr)
        return 1
    if jobs:
        used = {t.key for j in jobs for t in targets_for(targets, j)}
        targets = [t for t in targets if t.key in used] or targets

    print(format_topology(targets))
    if args.show_topology:
        return 0
    if args.check_connections:
        from src.config.connection_check import check_targets, print_checks
        checks = check_targets(settings, targets)
        print_checks(checks)
        return 0 if all(c["ok"] for c in checks) else 1
    if args.report:
        for r in generate_reports(settings, logging_config, targets):
            print(f"Report: {r['tables_reported']} table(s) -> {r['bigquery_table']}  (local: {r['local_snapshot']})")
        return 0

    failed_targets: dict[str, str] = {}
    if args.provision or (migrating and not args.skip_provision):
        status = provision(settings, targets, logging_config)
        failed_targets = {k: v for k, v in status.items() if v != "OK"}
        print(f"\nProvisioning: {len(status) - len(failed_targets)} database(s) ready, {len(failed_targets)} failed")
        for k, v in failed_targets.items():
            print(f"  FAILED {k}: {v}")
        if args.provision:
            return 1 if failed_targets else 0
        targets = [t for t in targets if t.key not in failed_targets]

    batch_run_id = str(uuid.uuid4())
    all_results, plan_problems, catalog_cache = [], 0, {}
    for job in jobs:
        if not job.has_tables:
            continue
        plan = plan_job(settings, targets, job, catalog_cache=catalog_cache)
        _print_plan(job, plan)
        plan_problems += len(plan.failures) + (len(plan.unmatched) if args.strict else 0)
        if args.list or args.dry_run or not plan.items:
            continue
        print(f"\nRunning job '{job.name}': {len(plan.items)} table(s), parallel={args.parallel}, "
              f"max-per-server={args.max_per_server}, run id {batch_run_id}")
        results = run_items(settings, logging_config, plan.items, batch_run_id, parallel=args.parallel,
                            max_per_server=args.max_per_server, ignore_locks=args.ignore_locks)
        all_results.extend(results)
        all_results.extend({"table": label, "status": "SKIPPED", "reason": reason, "rows": 0}
                           for label, reason in plan.skipped)

    object_results = []
    for job in jobs:
        if not job.has_objects:
            continue
        oplan = plan_object_job(settings, targets, job, slim=False)
        _print_object_plan(job, oplan)
        plan_problems += len(oplan.failures) + (len(oplan.unmatched) if args.strict else 0)
        if args.list or args.dry_run or not oplan.items:
            continue
        print(f"\nExporting raw objects of job '{job.name}': {len(oplan.items)} database(s), run id {batch_run_id}")
        object_results.extend(run_object_items(settings, logging_config, oplan.items, batch_run_id,
                                               parallel=args.parallel, max_per_server=args.max_per_server,
                                               ignore_locks=args.ignore_locks))

    if args.list or args.dry_run:
        return 1 if plan_problems else 0

    by_status: dict[str, list] = {}
    for r in all_results:
        by_status.setdefault(r["status"], []).append(r)
    if all_results:
        print("\n" + "=" * 78 + "\nSummary: " + ", ".join(f"{len(v)} {k}" for k, v in sorted(by_status.items())))
    for r in by_status.get("FAILED", []):
        print(f"  FAILED  {r['table']}: {r.get('error')}")
    for r in all_results:
        if r.get("reconciled") is False:
            print(f"  MISMATCH {r['table']}: source {r.get('source_rows')} vs BigQuery {r.get('target_rows')}")
    if object_results:
        print("\nRaw objects (original T-SQL, not translated):")
        for r in sorted(object_results, key=lambda x: x["database"]):
            if r["status"] == "SUCCESS":
                kinds = ", ".join(f"{v} {FOLDERS.get(k, k)}" for k, v in sorted((r.get("by_kind") or {}).items()))
                changes = (f"new={r['new']} changed={r['changed']} unchanged={r['unchanged']} dropped={r['dropped']}"
                           if r.get("bigquery_table") else f"exported={r.get('exported', r['objects'])} (no BigQuery)")
                print(f"  OK      {r['database']}: {r['objects']} object(s) [{kinds or 'none'}]  {changes}"
                      + (f" encrypted={r['encrypted']}" if r.get("encrypted") else "")
                      + (f" no_permission={r['no_permission']}" if r.get("no_permission") else ""))
                for label, key in (("files", "local_dir"), ("gcs", "gcs_prefix"), ("table", "bigquery_table")):
                    if r.get(key):
                        print(f"            {label}: {r[key]}")
            else:
                print(f"  {r['status']:<7s} {r['database']}: {r.get('error') or r.get('reason')}")
    if all_results:
        generate_reports(settings, logging_config, targets)
    bad = (bool(by_status.get("FAILED")) or bool(failed_targets) or plan_problems > 0 or (args.strict and problems)
           or any(r["status"] == "FAILED" for r in object_results))
    return 1 if bad else 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit("\nInterrupted.")
