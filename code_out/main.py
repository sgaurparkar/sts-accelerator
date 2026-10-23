import argparse
import copy
import time
import uuid
import yaml

from src.planner.table_planner import (
    build_table_plan,
    get_table_plan,
    TableNotFoundError,
    AmbiguousTableError,
    SchemaNotFoundError,
)
from src.config.region_resolver import (
    RegionError,
    available_regions,
    is_all,
    regions_to_run,
    resolve_region,
    select_targets, target_info, multi_project_enabled,
)
from src.inventory.table_inventory import (
    InventoryError,
    age_hours,
    inventory_path,
    load_inventory,
    locate,
    locate_regions,
    refresh_inventory,
)
from src.pipeline.table_pipeline import run_table
from src.config.network_check import all_ok, check_private_path, format_report, transfer_mode
from src.reporting.report_generator import ReportGenerator
from src.pipeline.timing import fmt_duration
from src.gcp.project_manager import ensure_project, ProjectError


def run_one(settings, logging_config, table_cfg, run_id, stage="bq", force=False):
    print(f"\n{'=' * 70}")
    print(f"Table:  {table_cfg['schema']}.{table_cfg['name']}  "
          f"(load_mode={table_cfg['load_mode']}, pk={table_cfg['primary_key']})")
    print(f"Run ID: {run_id}")
    if stage == "gcs":
        print("Stage:  stopping after GCS (BigQuery load will be skipped)")
    if force:
        print("Force:  reloading even if the table already COMPLETED")
    t0 = time.monotonic()
    result = run_table(settings, logging_config, table_cfg, run_id, stage=stage, force=force)
    result.setdefault("elapsed_seconds", round(time.monotonic() - t0, 1))
    print(f"Done: {result}")
    print(f"Total time for {table_cfg['schema']}.{table_cfg['name']}: {fmt_duration(result['elapsed_seconds'])}")
    return result


def load_yaml(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f)
    except FileNotFoundError:
        raise SystemExit(f"Config file not found: {path}")
    except yaml.YAMLError as e:
        raise SystemExit(f"Could not parse {path}: {e}")


def split_schema_table(raw: str, cli_schema: str | None) -> tuple[str, str | None]:
    if "." in raw:
        parts = raw.split(".")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            raise SystemExit(
                f"--table '{raw}' looks like a schema.table shorthand but isn't "
                "valid — use exactly one '.', e.g. --table dbo.customers."
            )
        dotted_schema, dotted_table = parts
        if cli_schema and cli_schema != dotted_schema:
            raise SystemExit(
                f"--table '{raw}' says schema '{dotted_schema}' but --schema "
                f"'{cli_schema}' was also given — remove one, they disagree."
            )
        return dotted_table, dotted_schema
    return raw, cli_schema


def _dataset_label(src: dict) -> str:
    base = src.get("dataset_prefix") or src["region_slug"]
    return base + " (tables: Orders, <schema>__Orders)" if src.get("flat_naming") else base + "_<schema>"


def announce_region(settings) -> None:
    src = settings["source"]
    if not src.get("region"):
        return
    g = settings["gcp"]
    print(f"Region: {src['region']}  ->  {src['host']}"
          + (f"  (resource group {src['resource_group']}, server {src.get('server')})" if src.get("resource_group") else ""))
    print(f"GCP:    project={g['project_id']} | bucket=gs://{g['gcs_bucket']} | control dataset={g['bq_dataset']} | "
          f"data dataset={_dataset_label(src)} | location={g.get('location')}")


def show_inventory(settings) -> None:
    inv = load_inventory(inventory_path(settings))
    if not inv["regions"]:
        print(f"No table inventory yet ({inventory_path(settings)}). Build it with: "
              "python3 main.py --refresh-inventory")
        return
    age = age_hours(inv)
    print(f"Table inventory {inventory_path(settings)}"
          + (f"  (built {age:.1f} h ago)" if age is not None else ""))
    for region, e in sorted(inv["regions"].items()):
        state = "OK" if e.get("status") == "OK" else f"ERROR: {e.get('error')}"
        print(f"  {region:24s} {len(e.get('tables', [])):6d} table(s)  {e.get('host')}  [{state}]")


def find_table_command(settings, args) -> None:
    table_name, schema = split_schema_table(args.find_table, args.schema)
    hits, inv = locate(settings, table_name, schema, auto_refresh=False)
    if not hits:
        raise SystemExit(f'"{args.find_table}" is not in the inventory. Run '
                         "`python3 main.py --refresh-inventory` if it is new.")
    for h in hits:
        print(f"  {h['region']:24s} {h['schema']}.{h['name']}   {h['host']}")


def build_parser():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", help="Table name to migrate — bare ('customers') or "
                                         "schema-qualified ('dbo.customers'); discovered "
                                         "dynamically from SQL Server")
    parser.add_argument("--schema", help="Schema to use with --table (to disambiguate a table "
                                          "name that exists in more than one schema) or to "
                                          "scope --all / --list to a single schema. Omit to "
                                          "span every discovered schema.")
    parser.add_argument("--region", nargs="+", metavar="NAME",
                         help="Azure region of the source SQL server, exactly as Azure shows it — "
                              "quotes optional: --region North Central US. Use 'all' for every "
                              "configured region. Optional with --table (the table inventory finds "
                              "the server); required with --all / --list / --report / --check-network "
                              "when `regions:` is configured in config/settings.yaml.")
    parser.add_argument("--resource-group", nargs="+", metavar="NAME", dest="resource_group",
                         help="Azure resource group to migrate (every SQL server configured under it). "
                              "Use 'all' for every resource group. Combine with --server to pick one server.")
    parser.add_argument("--server", metavar="NAME",
                         help="SQL server (name as in settings `server:`, or the first part of its host) "
                              "inside the chosen resource group(s).")
    parser.add_argument("--list-projects", action="store_true", dest="list_projects",
                         help="Show which GCP project / bucket / dataset prefix each configured target maps to "
                              "and exit. Creates nothing.")
    parser.add_argument("--cutoff-date", default=None, metavar="YYYY-MM-DD",
                         help="Only transfer rows whose updatedAt is on/before this date. "
                              "Overrides extraction.cutoff_date in settings.yaml for this run.")
    parser.add_argument("--check-network", action="store_true",
                         help="Check that the region's SQL server name (and, in sts mode, the Blob storage "
                              "name) resolves to a private address AND that address answers on its "
                              "port, i.e. the VPN tunnel is really used. Exits. Needs --region.")
    parser.add_argument("--list-regions", action="store_true",
                         help="Show the configured regions and exit")
    parser.add_argument("--refresh-inventory", action="store_true",
                         help="Scan the SQL servers (all regions, or just --region) and rebuild the table "
                              "inventory that lets --table work without --region. Exits.")
    parser.add_argument("--show-inventory", action="store_true",
                         help="Show what the table inventory knows (tables per region, age) and exit")
    parser.add_argument("--find-table", metavar="NAME",
                         help="Show which region(s)/server(s) hold a table, from the inventory, and exit "
                              "(nothing is migrated)")
    parser.add_argument("--all", action="store_true", help="Migrate every dynamically discovered "
                                                             "table (every schema, unless --schema is given)")
    parser.add_argument("--list", action="store_true", help="List every dynamically discovered "
                                                              "table and exit")
    parser.add_argument("--report", action="store_true",
                         help="(Re)generate the tablewise status report and exit")
    parser.add_argument("--force", action="store_true",
                         help="Re-run tables even if their last run already COMPLETED. "
                              "By default a COMPLETED load_mode=full table is skipped.")
    parser.add_argument("stage", nargs="?", choices=["gcs", "bq"], default="bq",
                         help="How far to run the pipeline: 'gcs' stops once each batch "
                              "has landed in GCS (direct mode: extract -> GCS; sts mode: extract -> Azure -> STS -> GCS), "
                              "'bq' (default) continues on to the BigQuery staging load + "
                              "merge, same as omitting this argument.")
    return parser


def execute(args, settings, logging_config) -> None:
    """Runs the selected mode against ONE (already region-resolved) settings dict.
    Failures end in SystemExit so a multi-region run can carry on with the next region."""
    if getattr(args, "cutoff_date", None):
        settings.setdefault("extraction", {})["cutoff_date"] = args.cutoff_date
    try:
        if args.check_network:
            results = check_private_path(settings, probe=True)
            print(f"VPN path check for {settings['source'].get('region') or 'default server'}:")
            print(format_report(results))
            if not all(r["private"] for r in results):
                raise SystemExit("Not all names resolve to private addresses: traffic would bypass the VPN tunnel.")
            if not all_ok(results):
                raise SystemExit("A private address was found but it is not reachable: DNS is right, but the "
                                 "route through the VPN tunnel (or an Azure NSG) is not.")
            what = "SQL" if transfer_mode(settings) == "direct" else "SQL and Blob"
            print(f"OK: {what} traffic from this machine uses the private path.")
            return

        if args.report:
            result = ReportGenerator(settings, logging_config).generate()
            print(f"Report generated: {result['tables_reported']} tables")
            print(f"  BigQuery table:  {result['bigquery_table']}")
            print(f"  Local snapshot:  {result['local_snapshot']}")
            return

        if args.list:
            tables = build_table_plan(settings, schema=args.schema)
            if not tables:
                scope = f" in schema '{args.schema}'" if args.schema else ""
                print(f"No tables discovered{scope}.")
                return
            for t in tables:
                print(f"{t['schema']}.{t['name']:30s} load_mode={t['load_mode']:11s} "
                      f"pk={t['primary_key']}")
            return

        if args.all:
            tables = build_table_plan(settings, schema=args.schema)
            if not tables:
                scope = f"schema '{args.schema}'" if args.schema else "source.schemas in config/settings.yaml"
                raise SystemExit(f"No tables were discovered — check {scope}.")

            batch_run_id = str(uuid.uuid4())
            print(f"Batch run ID: {batch_run_id}")
            print(f"Migrating {len(tables)} table(s): "
                  f"{', '.join(t['schema'] + '.' + t['name'] for t in tables)}")
            if args.stage == "gcs":
                print("Stage:  stopping after GCS for every table (BigQuery load will be skipped)")

            succeeded, skipped, failed = [], [], []
            batch_t0 = time.monotonic()
            for table_cfg in tables:
                # each table gets its own run_id (like migrate_table.expand() does
                # per Airflow task instance) so checkpoints/audit rows don't collide
                run_id = f"{batch_run_id}:{table_cfg['schema']}.{table_cfg['name']}"
                label = f"{table_cfg['schema']}.{table_cfg['name']}"
                try:
                    result = run_one(settings, logging_config, table_cfg, run_id,
                                      stage=args.stage, force=args.force)
                    (skipped if result.get("status") == "SKIPPED" else succeeded).append(label)
                except Exception as exc:
                    # don't let one table's failure stop the rest of the batch —
                    # same all_done/resume-later behavior as the Airflow DAG
                    print(f"FAILED | Table={label} | Reason={exc}")
                    failed.append(label)

            print(f"\n{'=' * 70}")
            print(f"Batch complete: {len(succeeded)} succeeded, {len(skipped)} skipped "
                  f"(already COMPLETED), {len(failed)} failed | total time {fmt_duration(time.monotonic() - batch_t0)}")
            if succeeded:
                print(f"  Succeeded: {', '.join(succeeded)}")
            if skipped:
                print(f"  Skipped:   {', '.join(skipped)}  (use --force to reload)")
            if failed:
                print(f"  Failed:    {', '.join(failed)}")

            try:
                report = ReportGenerator(settings, logging_config).generate()
                print(f"Report generated: {report['tables_reported']} tables")
                print(f"  BigQuery table:  {report['bigquery_table']}")
                print(f"  Local snapshot:  {report['local_snapshot']}")
            except Exception as exc:       # a report problem must not hide the migration result
                print(f"WARNING: report generation failed: {exc}")

            if failed:
                raise SystemExit(f"{len(failed)} table(s) failed: {', '.join(failed)}")
            return

        # --table
        table_name, schema = split_schema_table(args.table, args.schema)
        table_cfg = get_table_plan(settings, table_name, schema=schema)
        run_id = str(uuid.uuid4())
        run_one(settings, logging_config, table_cfg, run_id, stage=args.stage, force=args.force)

    except (TableNotFoundError, AmbiguousTableError, SchemaNotFoundError) as e:
        raise SystemExit(f"Error: {e}")
    except RuntimeError as e:
        # e.g. missing env vars / bad connection config surfaced from
        # src/discovery — fail with a clear one-line message instead of
        # a raw traceback.
        raise SystemExit(f"Error: {e}")


def main():
    parser = build_parser()
    args = parser.parse_args()

    # `--region North Central US gcs` -> the stage word is swallowed by --region; give it back.
    region_words = list(args.region or [])
    if region_words and region_words[-1].lower() in ("gcs", "bq"):
        stage_word = region_words.pop().lower()
        if args.stage != "bq" and args.stage != stage_word:
            raise SystemExit(f"Conflicting stage arguments: '{args.stage}' and '{stage_word}'.")
        args.stage = stage_word
    args.region = " ".join(region_words).strip() or None
    rg_words = list(args.resource_group or [])
    if rg_words and rg_words[-1].lower() in ("gcs", "bq"):
        stage_word = rg_words.pop().lower()
        if args.stage != "bq" and args.stage != stage_word:
            raise SystemExit(f"Conflicting stage arguments: '{args.stage}' and '{stage_word}'.")
        args.stage = stage_word
    args.resource_group = " ".join(rg_words).strip() or None

    modes_selected = sum(bool(x) for x in (args.table, args.all, args.list, args.report, args.list_regions,
                                           args.check_network, args.refresh_inventory, args.show_inventory,
                                           args.find_table, args.list_projects))
    if modes_selected == 0:
        raise SystemExit("Provide --table <n>, --all, --list, --report, --check-network, --list-regions, --refresh-inventory, --show-inventory, --find-table or --list-projects")
    if modes_selected > 1:
        raise SystemExit("Use only one of --table, --all, --list, --report, --check-network, --list-regions, "
                         "--refresh-inventory, --show-inventory, --find-table, --list-projects at a time.")
    if args.schema and not (args.table or args.all or args.list):
        raise SystemExit("--schema only applies together with --table, --all, or --list.")
    if args.table is not None and not str(args.table).strip():
        raise SystemExit("--table needs a table name.")

    settings = load_yaml("config/settings.yaml")
    if not isinstance(settings, dict):
        raise SystemExit("config/settings.yaml is empty or not a mapping.")

    if args.list_projects:
        try:
            keys = select_targets(settings, args.region, args.resource_group, args.server) \
                if (args.region or args.resource_group or args.server) else available_regions(settings)
        except RegionError as e:
            raise SystemExit(f"Error: {e}")
        print(f"multi_project: {'ENABLED' if multi_project_enabled(settings) else 'off (single project: ' + str(settings['gcp']['project_id']) + ')'}")
        print(f"{'target':24s} {'resource group':22s} {'server':26s} {'database':14s} project / bucket / dataset prefix")
        for k in keys:
            i = target_info(settings, k)
            tail = (f"{i['project_id']} / gs://{i['bucket']} / {i['dataset_prefix']}" + ("" if i.get("flat_naming") else "_<schema>")
                    if i.get("project_id") else "(multi_project off)")
            print(f"{i['target']:24s} {i['resource_group'] or '-':22s} {i['server']:26s} {i['database']:14s} {tail}")
        return

    if args.list_regions:
        try:
            names = available_regions(settings)
        except RegionError as e:
            raise SystemExit(f"Error: {e}")
        if not names:
            print("No `regions:` configured in config/settings.yaml.")
            return
        print("Configured regions (use with --region):")
        for r in names:
            e = settings["regions"][r] if isinstance(settings["regions"][r], dict) else {}
            host = str(e.get("host") or "")
            status = "host NOT set" if (not host or "<" in host) else host
            print(f"  {r:22s} gcp_location={e.get('gcp_location') or '(missing)':20s} {status}")
        return

    try:
        if args.refresh_inventory:
            inv = refresh_inventory(settings, args.region)
            bad = [r for r, e in inv["regions"].items() if e.get("status") != "OK"]
            total = sum(len(e.get("tables", [])) for e in inv["regions"].values())
            print(f"Inventory: {total} table(s) across {len(inv['regions'])} server(s).")
            if bad:
                raise SystemExit(f"Could not scan: {', '.join(bad)} (previous entries kept).")
            return
        if args.show_inventory:
            show_inventory(settings)
            return
        if args.find_table:
            find_table_command(settings, args)
            return
    except (InventoryError, RegionError) as e:
        raise SystemExit(f"Error: {e}")

    logging_config = load_yaml("config/logging.yaml")

    per_region_args = {}
    try:
        by_group = bool(args.resource_group or args.server)
        if args.table and settings.get("regions") and not by_group and (not args.region or is_all(args.region)):
            # --table without a specific region: the table inventory says which server(s) hold it.
            table_name, schema = split_schema_table(args.table, args.schema)
            found = locate_regions(settings, table_name, schema)
            if not args.region and len(found) > 1:
                where = ", ".join(f'"{r}"' for r in found)
                raise SystemExit(f'Table "{table_name}" exists on more than one server: {where}. '
                                 'Pick one with --region "<name>", or use --region all to migrate it '
                                 "from every one of them.")
            targets = list(found)
            for r, hit in found.items():
                a = copy.copy(args)
                a.table, a.schema = hit["name"], schema or hit["schema"]
                per_region_args[r] = a
            print(f"Inventory: {table_name} -> " + ", ".join(
                f"{r} ({(h['schema'] or '?')}.{h['name']})" for r, h in found.items()))
        else:
            targets = select_targets(settings, args.region, args.resource_group, args.server)
    except (RegionError, InventoryError) as e:
        raise SystemExit(f"Error: {e}")

    multi = len(targets) > 1
    failures = []
    for name in targets:
        if multi:
            print(f"\n{'#' * 70}\n# Region: {name}\n{'#' * 70}")
        try:
            resolved = resolve_region(settings, name)
            announce_region(resolved)
            if multi_project_enabled(resolved) and not (args.check_network or args.list):
                ensure_project(resolved)
            execute(per_region_args.get(name, args), resolved, logging_config)
        except (RegionError, ProjectError) as e:
            err = SystemExit(f"Error: {e}")
            if not multi:
                raise err
            print(err)
            failures.append(name)
        except SystemExit as e:
            if e.code in (0, None):
                continue
            if not multi:
                raise
            print(f"[{name}] {e.code}")
            failures.append(name)

    if multi:
        print(f"\n{'=' * 70}\nAll-regions run complete: {len(targets) - len(failures)} OK, "
              f"{len(failures)} failed" + (f" ({', '.join(failures)})" if failures else ""))
        if failures:
            raise SystemExit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("\nInterrupted.")
