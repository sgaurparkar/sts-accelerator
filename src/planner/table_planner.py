import os

import yaml

from src.discovery.table_discovery import TableDiscovery
from src.discovery.schema_reader import SchemaReader


class TableNotFoundError(Exception):
    """Raised when --table (optionally with --schema) matches nothing
    that dynamic discovery found."""


class AmbiguousTableError(Exception):
    """Raised when --table matches a table name that exists in more
    than one schema and no --schema was given to disambiguate."""


class SchemaNotFoundError(Exception):
    """Raised when --schema names something dynamic discovery didn't
    find (typo, wrong case, schema with no base tables, etc.)."""


def _load_overrides(overrides_path: str) -> dict:
    """Keyed by (schema, name) so the same table name in two different
    schemas can be overridden independently. An override entry with no
    `schema` field (schema: None) is treated as a wildcard that applies
    to that table name in *any* schema, for backward compatibility with
    overrides files written before schema-scoping existed."""
    if not overrides_path or not os.path.exists(overrides_path):
        return {}
    with open(overrides_path) as f:
        data = yaml.safe_load(f) or {}
    out = {}
    for o in data.get("overrides", []) or []:
        name = o.get("name")
        if not name:
            continue
        out[(o.get("schema"), name)] = o
    return out


def _override_for(overrides: dict, schema: str, name: str) -> dict:
    if (schema, name) in overrides:
        return overrides[(schema, name)]
    return overrides.get((None, name), {})


def _dump_table_plan(plan: list[dict], path: str = "config/tables.yaml") -> None:
    """Auto-generated, read-only snapshot of the plan just built — for
    humans to look at, never for the pipeline to read back in. Rewritten
    from scratch every time build_table_plan() runs, so it can never go
    stale or drift from what SQL Server actually has. Safe to delete;
    it just reappears on the next run. Best-effort: a write failure here
    (e.g. read-only filesystem in some deploy environments) must never
    break the actual migration."""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        snapshot = {
            "_generated_by": "src/planner/table_planner.py — rewritten on every run, do not hand-edit",
            "tables": [
                {
                    "schema": t["schema"],
                    "name": t["name"],
                    "primary_key": t["primary_key"],
                    "synthetic_key": t["synthetic_key"],
                    "load_mode": t["load_mode"],
                }
                for t in plan
            ],
        }
        with open(path, "w") as f:
            yaml.safe_dump(snapshot, f, sort_keys=False)
    except OSError as e:
        print(f"[table_planner] WARNING: could not write {path} snapshot: {e}")


def build_table_plan(config: dict, schema: str | None = None) -> list[dict]:
    """The one function every entrypoint (DAGs, main.py) calls to get the
    live, dynamic list of tables to migrate.

    schema: optional. When given, only that schema is planned (and the
    schema must actually exist, or SchemaNotFoundError is raised) — used
    by `--all --schema X` / `--list --schema X` to scope a run to one
    schema. When omitted (the normal/DAG case), every schema configured
    in source.schemas is planned, or — if that's left unset — every
    schema dynamically discovered from SQL Server (see
    TableDiscovery.list_schemas()), which is what makes `--all` cover
    every schema with zero config change.

    The on-disk snapshot (config/tables.yaml) is only rewritten for the
    full, unscoped plan, so a one-off `--schema X` run never clobbers it
    with a partial view of the database.
    """
    discovery = TableDiscovery(config)
    schema_reader = SchemaReader(config)
    overrides = _load_overrides(config.get("overrides_file"))

    if schema:
        if not discovery.schema_exists(schema):
            known = discovery.list_schemas()
            raise SchemaNotFoundError(
                f"Schema '{schema}' was not found. Discovered schema(s): "
                f"{', '.join(known) if known else '(none — check source.host/database/schemas in config/settings.yaml)'}."
            )
        schemas = [schema]
    else:
        schemas = discovery.list_schemas()

    print(f"[table_planner] Planning tables across schema(s): {schemas} ...")

    all_entries = []
    for s in schemas:
        for name in discovery.list_tables(s):
            all_entries.append({"schema": s, "name": name})
    print(f"[table_planner] Discovered {len(all_entries)} table(s) in SQL Server: "
          f"{[e['schema'] + '.' + e['name'] for e in all_entries]}")

    plan = []
    for entry in all_entries:
        table_name, table_schema = entry["name"], entry["schema"]
        override = _override_for(overrides, table_schema, table_name)

        if override.get("exclude"):
            print(f"[table_planner] Excluding {table_schema}.{table_name} (tables_override.yaml)")
            continue

        primary_key = discovery.get_primary_key(table_name, table_schema)
        columns = schema_reader.get_columns(table_name, table_schema)

        synthetic_key = False
        if not primary_key:
            # No real primary key -> can't safely page, resume, or MERGE
            # without one. Rather than falling back to an unbounded
            # single-shot pull (breaks on large tables) or refusing to
            # migrate the table at all, inject a synthetic, physically
            # materialized sequential column (source_row_id) that the
            # extractor creates once in SQL Server and treats exactly
            # like a real primary key from here on — same batching, same
            # resumability, same zero-duplicate MERGE guarantee. See
            # SqlExtractor._ensure_synthetic_key_table for how it's built.
            synthetic_col = "source_row_id"
            existing_names = {c["name"].lower() for c in columns}
            if synthetic_col.lower() in existing_names:
                # Extremely unlikely, but don't silently collide with a
                # real column if this table happens to already have one
                # named source_row_id.
                synthetic_col = "_migration_row_id"
            columns = columns + [{"name": synthetic_col, "source_type": "bigint"}]
            primary_key = [synthetic_col]
            synthetic_key = True

        load_mode = override.get("load_mode") or "full"

        print(f"[table_planner] Planned {table_schema}.{table_name}: "
              f"primary_key={primary_key}, synthetic_key={synthetic_key}, "
              f"load_mode={load_mode}, columns={len(columns)}")

        plan.append({
            "name": table_name,
            "schema": table_schema,
            "primary_key": primary_key,
            "synthetic_key": synthetic_key,
            "columns": columns,
            "load_mode": load_mode,
            "source_query": f"SELECT * FROM [{table_schema}].[{table_name}]",
        })

    if schema is None:
        _dump_table_plan(plan, config.get("tables_snapshot_file", "config/tables.yaml"))
    print(f"[table_planner] Planning complete: {len(plan)} table(s) planned "
          f"({sum(1 for t in plan if t['load_mode'] == 'full')} full, "
          f"{sum(1 for t in plan if t['load_mode'] == 'incremental')} incremental)")
    return plan


def get_table_plan(config: dict, table_name: str, schema: str | None = None) -> dict:
    """Convenience for main.py --table <name> [--schema <schema>].

    Builds the full dynamic plan (across every schema, so the
    config/tables.yaml snapshot stays complete) and returns just the
    one table's entry.

    - schema given: looks the table up in exactly that schema. Raises
      TableNotFoundError if it's not there (even if the same name
      exists in a different schema — the error message says so).
    - schema omitted: matches the table name across every schema.
      Raises TableNotFoundError if there's no match anywhere, or
      AmbiguousTableError if the same table name exists in more than
      one schema (the error explains how to disambiguate).
    """
    plan = build_table_plan(config)
    matches = [t for t in plan if t["name"] == table_name]

    if schema:
        exact = [t for t in matches if t["schema"] == schema]
        if exact:
            return exact[0]
        if matches:
            other_schemas = ", ".join(sorted(t["schema"] for t in matches))
            raise TableNotFoundError(
                f"Table '{table_name}' was not found in schema '{schema}'. "
                f"It does exist in: {other_schemas}."
            )
        raise TableNotFoundError(
            f"Table '{table_name}' was not found by dynamic discovery in schema '{schema}'."
        )

    if not matches:
        raise TableNotFoundError(
            f"Table '{table_name}' was not found by dynamic discovery in any schema "
            "(check it exists in the source database and wasn't excluded in "
            "config/tables_override.yaml)."
        )
    if len(matches) > 1:
        found_schemas = sorted(t["schema"] for t in matches)
        example = f"{found_schemas[0]}.{table_name}"
        raise AmbiguousTableError(
            f"Table '{table_name}' exists in multiple schemas: {', '.join(found_schemas)}. "
            f"Disambiguate with --schema <schema>, e.g. --table {table_name} --schema {found_schemas[0]}, "
            f"or --table {example}."
        )
    return matches[0]
