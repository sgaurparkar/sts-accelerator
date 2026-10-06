import argparse
import sys
import uuid
import yaml

from src.planner.table_planner import (
    build_table_plan,
    get_table_plan,
    TableNotFoundError,
    AmbiguousTableError,
    SchemaNotFoundError,
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--table", help="Table name to migrate — bare ('customers') or "
                                         "schema-qualified ('dbo.customers'); discovered "
                                         "dynamically from SQL Server")
    parser.add_argument("--schema", help="Schema to use with --table (to disambiguate a table "
                                          "name that exists in more than one schema) or to "
                                          "scope --all / --list to a single schema. Omit to "
                                          "span every discovered schema.")
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
    args = parser.parse_args()

    modes_selected = sum(bool(x) for x in (args.table, args.all, args.list, args.report))
    if modes_selected == 0:
        raise SystemExit("Provide --table <name>, --all, --list, or --report")
    if modes_selected > 1:
        raise SystemExit("Use only one of --table, --all, --list, --report at a time.")
    if args.schema and not (args.table or args.all or args.list):
        raise SystemExit("--schema only applies together with --table, --all, or --list.")

    settings = load_yaml("config/settings.yaml")
    logging_config = load_yaml("config/logging.yaml")

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
                try:
                    result = run_one(settings, logging_config, table_cfg, run_id,
                                      stage=args.stage, force=args.force)
                    label = f"{table_cfg['schema']}.{table_cfg['name']}"
                    (skipped if result.get("status") == "SKIPPED" else succeeded).append(label)
                except Exception as exc:
                    # don't let one table's failure stop the rest of the batch —
                    # same all_done/resume-later behavior as the Airflow DAG
                    print(f"FAILED | Table={table_cfg['schema']}.{table_cfg['name']} | Reason={exc}")
                    failed.append(f"{table_cfg['schema']}.{table_cfg['name']}")

            print(f"\n{'=' * 70}")
            print(f"Batch complete: {len(succeeded)} succeeded, {len(skipped)} skipped "
                  f"(already COMPLETED), {len(failed)} failed")
            if succeeded:
                print(f"  Succeeded: {', '.join(succeeded)}")
            if skipped:
                print(f"  Skipped:   {', '.join(skipped)}  (use --force to reload)")
            if failed:
                print(f"  Failed:    {', '.join(failed)}")

            report = ReportGenerator(settings, logging_config).generate()
            print(f"Report generated: {report['tables_reported']} tables")
            print(f"  BigQuery table:  {report['bigquery_table']}")
            print(f"  Local snapshot:  {report['local_snapshot']}")

            if failed:
                raise SystemExit(1)
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


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        raise SystemExit("\nInterrupted.")