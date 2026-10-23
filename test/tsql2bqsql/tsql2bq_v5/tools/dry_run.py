#!/usr/bin/env python3
"""Dry-run every converted object in deploy order and write dry_run_results.csv.

    python tools/dry_run.py bigquery_sql [--project my-proj] [--location asia-south1]

Uses the `bq` CLI (gcloud SDK) with your current credentials. Nothing is created: every statement is sent with
--dry_run. A dry run of CREATE PROCEDURE checks the statement and the procedure body as far as BigQuery validates
it at creation time; errors that depend on data (UPDATE matching several rows, casts of bad values) only show up
when the procedure is CALLed on test data.
"""
import argparse
import csv
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("folder", help="output folder of convert.py (contains deploy_order.txt)")
    ap.add_argument("--project", help="GCP project to bill the dry runs to (default: gcloud config)")
    ap.add_argument("--location", help="BigQuery location, e.g. US or asia-south1")
    a = ap.parse_args(argv)
    bq = shutil.which("bq")
    if not bq:
        print("bq CLI not found: install the Google Cloud SDK or run this in Cloud Shell", file=sys.stderr)
        return 2
    folder = Path(a.folder)
    order = [l.strip() for l in (folder / "deploy_order.txt").read_text(encoding="utf-8").splitlines() if l.strip()]
    rows, bad = [], 0
    for rel in order:
        sql = (folder / rel).read_text(encoding="utf-8")
        cmd = [bq, "query", "--use_legacy_sql=false", "--dry_run", "--format=none"]
        if a.project:
            cmd.append(f"--project_id={a.project}")
        if a.location:
            cmd.append(f"--location={a.location}")
        # the SQL is passed on stdin, never through a shell, so its content cannot be interpreted as a command
        p = subprocess.run(cmd, input=sql, capture_output=True, text=True)
        ok = p.returncode == 0
        bad += not ok
        msg = " ".join((p.stderr or p.stdout or "").split())[:500]
        rows.append({"object": rel, "result": "OK" if ok else "ERROR", "message": "" if ok else msg})
        print(f"{'OK   ' if ok else 'ERROR'} {rel}" + ("" if ok else f"  {msg[:160]}"))
    out = folder / "dry_run_results.csv"
    with out.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["object", "result", "message"])
        w.writeheader()
        w.writerows(rows)
    print(f"\n{len(rows) - bad} OK, {bad} errors -> {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
