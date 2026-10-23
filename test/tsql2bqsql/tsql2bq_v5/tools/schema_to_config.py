#!/usr/bin/env python3
"""Merge a COLUMN_NAME,DATA_TYPE csv (see export_schema.sql) into config.json -> "column_types".

    python tools/schema_to_config.py columns.csv [config.json]
"""
import csv
import json
import sys
from pathlib import Path


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    csv_path = Path(argv[0])
    cfg_path = Path(argv[1]) if len(argv) > 1 else Path(__file__).resolve().parent.parent / "config.json"
    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    types = cfg.setdefault("column_types", {})
    with csv_path.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            name = (row.get("COLUMN_NAME") or row.get("column_name") or "").strip()
            typ = (row.get("DATA_TYPE") or row.get("data_type") or "").strip().lower()
            if name and typ:
                types[name] = typ
    cfg_path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
    print(f"{len(types)} column types now in {cfg_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
