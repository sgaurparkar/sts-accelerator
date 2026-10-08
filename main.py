import argparse
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
    regions_to_run,
    resolve_region,
)
from src.pipeline.table_pipeline import run_table
from src.reporting.report_generator import ReportGenerator


def run_one(settings, logging_config, table_cfg, run_id, stage="bq", force=False):
    print(f"\n{'=' * 70}")
    print(f"Table:  {table_cfg['schema']}.{table_cfg['name']}  "
          f"(load_mode={table_cfg['load_mode']}, pk={table_cfg['primary_key']})")
    print(f"Run ID: {run_id}")
    if stage == "gcs":
        print("Stage:  stopping after GCS (BigQuery load will be skipped)")
    if force:
        print("Force:  reloading even if the table already COMPLETED")
    result = run_table(settings, logging_config, table_cfg, run_id, stage=stage, force=force)
    print(f"Done: {result}")
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


def announce_region(settings) -> None:
    src = settings["source"]
    if not src.get("region"):
        return
    g = settings["gcp"]
    print(f"Region: {src['region']}  ->  {src['host']}")
    print(f"GCP:    bucket=gs://{g['gcs_bucket']} | control dataset={g['bq_dataset']} | "
          f"data datasets={src['region_slug']}_<schema> | location={g.get('location')}")


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
                              "configured region. Required with --table / --all / --list / --report "
                              "when `regions:` is configured in config/settings.yaml.")
    parser.add_argument("--list-regions", action="store_true",
                         help="Show the configured regions and exit")
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
                              "has landed in GCS (extract -> Azure -> STS transfer -> GCS), "
                              "'bq' (default) continues on to the BigQuery staging load + "
                              "merge, same as omitting this argument.")
    return parser


def execute(args, settings, logging_config) -> None:
    """Runs the selected mode against ONE (already region-resolved) settings dict.
    Failures end in SystemExit so a multi-region run can carry on with the next region."""
    try:
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
                  f"(already COMPLETED), {len(failed)} failed")
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

    modes_selected = sum(bool(x) for x in (args.table, args.all, args.list, args.report, args.list_regions))
    if modes_selected == 0:
        raise SystemExit("Provide --table <n>, --all, --list, --report, or --list-regions")
    if modes_selected > 1:
        raise SystemExit("Use only one of --table, --all, --list, --report, --list-regions at a time.")
    if args.schema and not (args.table or args.all or args.list):
        raise SystemExit("--schema only applies together with --table, --all, or --list.")
    if args.table is not None and not str(args.table).strip():
        raise SystemExit("--table needs a table name.")

    settings = load_yaml("config/settings.yaml")
    if not isinstance(settings, dict):
        raise SystemExit("config/settings.yaml is empty or not a mapping.")

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

    logging_config = load_yaml("config/logging.yaml")

    try:
        targets = regions_to_run(settings, args.region)
    except RegionError as e:
        raise SystemExit(f"Error: {e}")

    multi = len(targets) > 1
    failures = []
    for name in targets:
        if multi:
            print(f"\n{'#' * 70}\n# Region: {name}\n{'#' * 70}")
        try:
            resolved = resolve_region(settings, name)
            announce_region(resolved)
            execute(args, resolved, logging_config)
        except RegionError as e:
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
