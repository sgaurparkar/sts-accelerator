#!/usr/bin/env python3
"""Convert SQL Server stored procedures and views to BigQuery.

    python convert.py                      # stored_procedures_slq_sever -> bigquery_sql
    python convert.py --project my-proj --dataset my_dataset
"""
import argparse
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

# Always prefer the bundled sqlglot (vendor/): the converter relies on its exact AST (e.g. the `with_` / `from_`
# argument names), and an older pip-installed sqlglot makes most conversions silently wrong.
for cand in (HERE / "vendor", HERE / "sqlglot-main", HERE.parent / "sqlglot-main"):
    if (cand / "sqlglot").is_dir():
        sys.path.insert(0, str(cand))
        break

from tsql2bq.converter import CONFIG_WARNINGS, convert_path, load_config, write_report  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-i", "--input", default=str(HERE / "stored_procedures_slq_sever"),
                    help="folder (or single .sql file) with SQL Server procedures and views")
    ap.add_argument("-o", "--output", default=str(HERE / "bigquery_sql"), help="folder for BigQuery SQL")
    ap.add_argument("-c", "--config", default=str(HERE / "config.json"))
    ap.add_argument("--project", help="BigQuery project id (overrides config)")
    ap.add_argument("--dataset", help="default BigQuery dataset (overrides config)")
    ap.add_argument("--keep-print", action="store_true", help="convert PRINT to SELECT instead of a comment")
    ap.add_argument("--schema-mode", choices=["prefix", "drop"], help="how non-dbo schemas are mapped")
    ap.add_argument("--datetime-mode", choices=["datetime", "timestamp"],
                    help="GETDATE() -> CURRENT_DATETIME() (default) or CURRENT_TIMESTAMP()")
    ap.add_argument("--out-param-mode", choices=["INOUT", "OUT"], help="how OUTPUT parameters are declared")
    ap.add_argument("--return-status-mode", choices=["drop", "out_param"],
                    help="RETURN <n>: drop it (default) or keep it in an OUT return_status parameter")
    ap.add_argument("--fail-on-review", action="store_true",
                    help="exit with code 3 when any object needs review (for CI pipelines)")
    a = ap.parse_args(argv)

    try:
        cfg = load_config(Path(a.config))
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    for w in CONFIG_WARNINGS:
        print(f"WARNING: {w}", file=sys.stderr)
    if a.project:
        cfg.project = a.project
    if a.dataset:
        cfg.default_dataset = a.dataset
    if a.keep_print:
        cfg.keep_print = True
    if a.schema_mode:
        cfg.schema_mode = a.schema_mode
    if a.datetime_mode:
        cfg.datetime_mode = a.datetime_mode
    if a.out_param_mode:
        cfg.out_param_mode = a.out_param_mode
    if a.return_status_mode:
        cfg.return_status_mode = a.return_status_mode

    inp, out = Path(a.input), Path(a.output)
    if not inp.exists():
        print(f"Input not found: {inp}", file=sys.stderr)
        return 2
    print(f"Input   : {inp}\nOutput  : {out}\nProject : {cfg.project}   default dataset: {cfg.default_dataset}")
    results = convert_path(inp, out, cfg)
    write_report(results, out, cfg)

    ok = sum(r.status == "ok" for r in results)
    rv = sum(r.status == "review" for r in results)
    bad = sum(r.status in ("failed",) for r in results)
    sk = sum(r.status == "skipped" for r in results)
    print(f"\nDone: {ok} clean, {rv} need review, {bad} failed, {sk} skipped  (see {out / 'conversion_report.md'})")
    if bad:
        return 1
    return 3 if (a.fail_on_review and rv) else 0


if __name__ == "__main__":
    sys.exit(main())
