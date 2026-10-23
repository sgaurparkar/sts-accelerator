"""
table_planner.py — turns one database's catalog + one job into table configs.

For every selected table it decides, from the live catalog (nothing hand-typed):

  load mode      job --mode, or (mode auto, the default) tables_override.yaml load_mode, else
                 the LIFECYCLE: incremental when the table has a key and a watermark (its
                 first run is a full INITIAL load, every later run is incremental), else
                 full once (later runs are skipped as COMPLETED unless --force)
  merge key      primary key -> unique index (NOT NULL, unfiltered) -> key_columns
                 override -> none
  strategy       keyset   page by the key (resumable)                     tables with a key
                 stream   one forward-only read, no writes to the source   full load, no key (default)
                 shadow   materialised row-number table in the source      full load, no key (opt-in)
  merge          insert_only (full / append-only incremental), upsert (incremental with a
                 change watermark, partial), append (no key, after truncate)
  watermark      incremental only: override -> rowversion -> first column named in
                 incremental.watermark_columns -> single ever-increasing key column
  filters        full: extraction.cutoff_date on extraction.cutoff_column
                 partial: --where and/or --date-column/--from/--to

A table that can't be run in the requested mode is returned as SKIPPED with
the reason, never silently dropped.
"""
import hashlib
import json
import os
import re

import yaml

from src.catalog.catalog_reader import CatalogReader
from src.common import log
from src.planner.type_mapper import (
    INT_TYPES,
    TEMPORAL_TYPES,
    WATERMARK_TYPES,
    base_type,
    map_column,
)
from src.selection.run_spec import Job, normalize_mode
from src.selection.table_selector import TableSelector
from src.topology.naming import bq_column_names, bq_table_name

LOG = log.get("planner")

DEFAULT_WATERMARK_COLUMNS = ["updatedAt", "updated_at", "UpdatedDate", "ModifiedDate", "ModifiedOn",
                             "modified_at", "LastModified", "LastModifiedDate", "LastUpdated", "UpdateDate"]
SYNTHETIC_KEY = "source_row_id"


class TableNameClashError(Exception):
    """Two source tables would land in the same BigQuery table."""


class CutoffColumnError(Exception):
    """extraction.cutoff_date is set but a table lacks the cutoff column (missing_cutoff_column: error)."""


_SCOPE_FIELDS = ("resource_group", "server", "database")


def _norm(x) -> str:
    return re.sub(r"[^a-z0-9]", "", str(x or "").lower())


# ---------------------------------------------------------------- overrides --
def load_overrides(path: str | None, source_cfg: dict | None = None) -> dict:
    """tables_override.yaml entries that apply to this database, keyed by
    (schema-or-None, table) lower-cased; the most specific entry wins."""
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    src = source_cfg or {}
    entries = []
    for o in data.get("overrides") or []:
        if not isinstance(o, dict) or not o.get("name"):
            continue
        if any(o.get(k) and _norm(o[k]) != _norm(src.get(k)) for k in _SCOPE_FIELDS):
            continue
        entries.append(o)
    entries.sort(key=lambda o: sum(1 for k in _SCOPE_FIELDS if o.get(k)))
    out = {}
    for o in entries:
        out[((o.get("schema") or "").lower() or None, str(o["name"]).lower())] = o
    return out


def override_for(overrides: dict, schema: str, name: str) -> dict:
    return overrides.get((schema.lower(), name.lower())) or overrides.get((None, name.lower())) or {}


# ------------------------------------------------------------------ helpers --
def _find(columns: list[dict], name: str | None) -> dict | None:
    if not name:
        return None
    low = str(name).lower()
    return next((c for c in columns if c["name"].lower() == low), None)


def scope_key_for(mode: str, partial: dict | None) -> str:
    if mode != "partial":
        return mode
    digest = hashlib.sha1(json.dumps(partial or {}, sort_keys=True, default=str).encode()).hexdigest()[:12]
    return f"partial:{digest}"


def _cutoff_for(settings: dict, job: Job) -> str | None:
    if job.cutoff_date is not None:
        return None if str(job.cutoff_date).strip().lower() in ("", "none", "null", "off") else str(job.cutoff_date)
    value = (settings.get("extraction") or {}).get("cutoff_date")
    return None if value in (None, "") else str(value)


def _skip(schema: str, name: str, mode: str, reason: str) -> dict:
    return {"schema": schema, "name": name, "mode": mode, "skip_reason": reason}


def _pick_watermark(columns, key_cols, override, inc_cfg) -> tuple[dict | None, str | None]:
    """-> (watermark spec, reason-if-none)."""
    wanted = override.get("watermark_column") or inc_cfg.get("watermark_column")
    if wanted:
        col = _find(columns, wanted)
        if not col:
            return None, f'watermark_column "{wanted}" does not exist'
        if base_type(col["data_type"]) not in WATERMARK_TYPES:
            return None, f'watermark_column "{wanted}" has type {col["data_type"]}, not date/time/number/rowversion'
        kind = "rowversion" if base_type(col["data_type"]) in ("timestamp", "rowversion") else "value"
        return {"column": col["name"], "kind": kind, "is_key": [col["name"]] == key_cols}, None

    rv = next((c for c in columns if base_type(c["data_type"]) in ("timestamp", "rowversion")), None)
    if rv:
        return {"column": rv["name"], "kind": "rowversion", "is_key": False}, None

    for cand in inc_cfg.get("watermark_columns") or DEFAULT_WATERMARK_COLUMNS:
        col = _find(columns, cand)
        if col and base_type(col["data_type"]) in TEMPORAL_TYPES:
            return {"column": col["name"], "kind": "value", "is_key": False}, None

    if len(key_cols) == 1:
        col = _find(columns, key_cols[0])
        t = base_type(col["data_type"]) if col else ""
        if t in INT_TYPES or t in TEMPORAL_TYPES or (t in ("decimal", "numeric") and not col.get("scale")):
            # append-only: new rows have a larger key (identity / sequence / insert time)
            return {"column": col["name"], "kind": "value", "is_key": True}, None
    return None, ("no watermark: no rowversion column, none of incremental.watermark_columns, "
                  "and the key is not a single increasing column")


# ------------------------------------------------------------------ planning --
def plan_table(entry: dict, settings: dict, job: Job, overrides: dict | None = None) -> dict | None:
    """One catalog entry -> table config, a skip record ({"skip_reason": ...}),
    or None when the table isn't part of this job (only_modes)."""
    schema, name = entry["schema"], entry["name"]
    override = override_for(overrides or {}, schema, name)
    gcp_cfg = settings.get("gcp") or {}
    extraction = settings.get("extraction") or {}
    inc_cfg = settings.get("incremental") or {}

    lifecycle = job.mode == "auto" and not override.get("load_mode")
    if job.mode != "auto":
        mode = job.mode
    else:
        mode = "incremental" if lifecycle else normalize_mode(override.get("load_mode"))
    if job.only_modes and not lifecycle and mode not in job.only_modes:
        return None
    load_plan = None
    if override.get("exclude"):
        return _skip(schema, name, mode, "excluded in tables_override.yaml")
    if not entry.get("columns"):
        return _skip(schema, name, mode, "no columns visible to this login")

    raw_columns = [dict(c) for c in entry["columns"]]

    # ---- merge key --------------------------------------------------------
    key_cols, key_kind = [], "none"
    if override.get("key_columns"):
        wanted = override["key_columns"] if isinstance(override["key_columns"], list) else [override["key_columns"]]
        found = [_find(raw_columns, k) for k in wanted]
        if not all(found):
            return _skip(schema, name, mode, f"key_columns {wanted} not all found")
        key_cols, key_kind = [c["name"] for c in found], "override"
    elif entry.get("primary_key"):
        key_cols, key_kind = list(entry["primary_key"]), "primary_key"
    elif entry.get("unique_keys"):
        key_cols, key_kind = list(min(entry["unique_keys"], key=len)), "unique_index"

    # ---- incremental watermark (may fall back to full) ---------------------
    watermark = None
    if mode == "incremental":
        if not key_cols:
            if not lifecycle:
                return _skip(schema, name, mode, "incremental needs a primary key, unique index or key_columns override")
            mode, load_plan = "full", "full once (no key for incremental), then skipped unless --force"
        watermark, reason = _pick_watermark(raw_columns, key_cols, override, inc_cfg) if mode == "incremental" \
            else (None, None)
        if mode == "incremental" and not watermark and lifecycle:
            mode, load_plan = "full", f"full once ({reason}), then skipped unless --force"
        elif mode == "incremental" and not watermark:
            fallback = str(inc_cfg.get("no_watermark") or "skip").lower()
            if fallback != "full":
                return _skip(schema, name, mode, reason)
            LOG.warning("%s.%s: %s — running a full load instead (incremental.no_watermark: full)",
                        schema, name, reason)
            mode = "full"

    if lifecycle and job.only_modes and mode not in job.only_modes:
        return None
    if mode == "incremental" and load_plan is None:
        load_plan = ("incremental on " + watermark["column"]
                     + (" (append-only key)" if watermark.get("is_key") else "")
                     + (" — first run: full INITIAL load" if lifecycle else ""))

    # ---- strategy / merge -------------------------------------------------
    if key_cols:
        strategy = "keyset"
        if mode == "full":
            merge = "insert_only"
        elif mode == "incremental":
            merge = "insert_only" if watermark["is_key"] else "upsert"
        else:
            merge = "upsert"
    else:
        if mode == "partial":
            return _skip(schema, name, mode, "partial needs a primary key, unique index or key_columns override")
        no_pk = str(override.get("no_pk_strategy") or extraction.get("no_pk_strategy") or "stream").lower()
        if no_pk == "exclude":
            return _skip(schema, name, mode, "no primary key / unique index (extraction.no_pk_strategy: exclude)")
        if no_pk == "shadow_table":
            strategy, merge = "shadow", "insert_only"
            synth = SYNTHETIC_KEY if not _find(raw_columns, SYNTHETIC_KEY) else "_migration_row_id"
            raw_columns.append({"name": synth, "data_type": "bigint", "is_nullable": False, "synthetic": True})
            key_cols, key_kind = [synth], "synthetic"
        elif no_pk == "stream":
            strategy, merge = "stream", "append"
        else:
            raise ValueError(f'extraction.no_pk_strategy "{no_pk}" — use stream, shadow_table or exclude.')

    # ---- columns ----------------------------------------------------------
    bq_names = bq_column_names([c["name"] for c in raw_columns], gcp_cfg.get("column_naming") or "sanitize")
    columns = []
    for c, bq in zip(raw_columns, bq_names):
        mapped = map_column(c, settings.get("types"))
        mapped["bq_name"] = bq
        mapped.pop("arrow", None)          # rebuilt at write time (keeps the config JSON-safe)
        columns.append(mapped)
    by_src = {c["name"]: c for c in columns}

    # ---- filters ----------------------------------------------------------
    filters = {"cutoff": None, "where": None, "date_range": None}
    if mode == "full":
        cutoff = _cutoff_for(settings, job)
        if cutoff:
            col_name = extraction.get("cutoff_column") or "updatedAt"
            col = _find(raw_columns, col_name)
            if col:
                filters["cutoff"] = {"column": col["name"], "value": cutoff}
            else:
                policy = str(extraction.get("missing_cutoff_column") or "error").lower()
                if policy == "exclude":
                    return _skip(schema, name, mode, f"no cutoff column '{col_name}' (missing_cutoff_column: exclude)")
                if policy != "skip_filter":
                    raise CutoffColumnError(
                        f"{schema}.{name} has no '{col_name}' column but a cutoff date is set. Set "
                        "extraction.cutoff_column, extraction.missing_cutoff_column (skip_filter / exclude), "
                        "or pass --cutoff-date none.")
                LOG.warning("%s.%s has no '%s' column — extracted without the cutoff filter", schema, name, col_name)
    elif mode == "partial":
        pf = job.partial_filter() or {}
        filters["where"] = pf.get("where")
        if pf.get("date_column"):
            col = _find(raw_columns, pf["date_column"])
            if not col:
                return _skip(schema, name, mode, f"no column '{pf['date_column']}' for the date range")
            if base_type(col["data_type"]) not in TEMPORAL_TYPES:
                return _skip(schema, name, mode, f"'{col['name']}' is {col['data_type']}, not a date/time column")
            filters["date_range"] = {"column": col["name"], "bq_column": by_src[col["name"]]["bq_name"],
                                     "from": pf.get("from"), "to": pf.get("to"),
                                     "replace": bool(pf.get("replace_range"))}

    if watermark:
        watermark["bq_column"] = by_src[watermark["column"]]["bq_name"]

    return {
        "schema": schema,
        "name": name,
        "bq_table": override.get("bq_table") or bq_table_name(schema, name, gcp_cfg),
        "mode": mode,
        "load_mode": mode,                         # kept for older reports / overrides wording
        "lifecycle": lifecycle,                    # auto: first run full, then incremental
        "load_plan": load_plan or mode,
        "scope_key": scope_key_for(mode, job.partial_filter()),
        "strategy": strategy,
        "merge": merge,
        "key": {"columns": key_cols, "kind": key_kind,
                "bq_columns": [by_src[k]["bq_name"] for k in key_cols]},
        "watermark": watermark,
        "filters": filters,
        "columns": columns,
        "row_estimate": entry.get("row_estimate"),
        "skip_reason": None,
    }


def check_name_clashes(plans: list[dict]) -> None:
    seen: dict[str, str] = {}
    for t in plans:
        if t.get("skip_reason"):
            continue
        key = t["bq_table"].lower()
        here = f"{t['schema']}.{t['name']}"
        other = seen.setdefault(key, here)
        if other != here:
            raise TableNameClashError(
                f"{other} and {here} would both land in BigQuery table '{t['bq_table']}'. Change "
                "gcp.table_naming.schema_prefix, or set bq_table for one of them in config/tables_override.yaml.")


def _snapshot(settings: dict, plans: list[dict]) -> None:
    base = settings.get("plans_dir")
    if not base:
        return
    gcp, src = settings.get("gcp") or {}, settings.get("source") or {}
    path = os.path.join(base, gcp.get("project_id") or "_unresolved", f"{gcp.get('dataset') or 'plan'}.yaml")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump({
                "_generated_by": "src/planner/table_planner.py — rewritten on every plan, do not edit",
                "azure": {k: src.get(k) for k in ("resource_group", "server", "database", "azure_location")},
                "gcp": {"project": gcp.get("project_id"), "dataset": gcp.get("dataset"), "location": gcp.get("location")},
                "tables": [{k: t.get(k) for k in ("schema", "name", "bq_table", "mode", "strategy", "merge",
                                                  "skip_reason")}
                           | {"key": (t.get("key") or {}).get("columns"),
                              "watermark": (t.get("watermark") or {}).get("column")} for t in plans],
            }, f, sort_keys=False)
    except OSError as e:
        LOG.warning("Could not write plan snapshot %s: %s", path, e)


def plan_database(settings: dict, job: Job, selector: TableSelector | None = None,
                  catalog: dict | None = None, reader: CatalogReader | None = None) -> list[dict]:
    """settings resolved for ONE database. Returns table configs (skipped ones
    carry skip_reason), largest tables first."""
    src = settings.get("source") or {}
    selector = selector or TableSelector(job.tables, job.exclude_tables, job.schemas)
    if not job.all_tables and not selector.applies_to_database(src.get("resource_group"), src.get("server"),
                                                               src.get("database")):
        return []
    if catalog is None:
        schemas = (src.get("schemas") or None) if src.get("schemas") not in ("*", ["*"]) else None
        catalog = (reader or CatalogReader(settings)).read(schemas=schemas)
    picked = selector.select(src.get("resource_group"), src.get("server"), src.get("database"), sorted(catalog))
    overrides = load_overrides(settings.get("overrides_file"), src)
    plans = []
    for key in picked:
        cfg = plan_table(catalog[key], settings, job, overrides)
        if cfg is not None:
            plans.append(cfg)
    check_name_clashes(plans)
    if job.all_tables and not job.schemas and not job.tables:
        _snapshot(settings, plans)
    plans.sort(key=lambda t: -(t.get("row_estimate") or 0))
    return plans


def plan_one(settings: dict, job: Job, schema: str, name: str, reader: CatalogReader | None = None) -> dict | None:
    """Re-plan a single table from a fresh catalog read (used by Airflow tasks,
    so XCom only carries table identities and the plan is never stale)."""
    catalog = (reader or CatalogReader(settings)).read(table=(schema, name))
    if (schema, name) not in catalog:
        return _skip(schema, name, job.mode, "table no longer exists in the source database")
    overrides = load_overrides(settings.get("overrides_file"), settings.get("source"))
    return plan_table(catalog[(schema, name)], settings, job, overrides)
