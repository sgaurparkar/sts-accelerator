"""
object_pipeline.py — RAW migration of views / stored procedures (no translation).

For ONE database (settings resolved by settings_for_target):

  plan_objects()   catalog index of the selected kinds, filtered by the job's object
                   patterns / schemas (no definitions read — cheap, used by --list/--dry-run)
  run_objects()
     lock objects:<rg>/<server>/<db>
     -> read definitions, parameters, columns, dependencies (sys.sql_modules & co.)
     -> dependency_level per object (the order a later translation must deploy them in)
     -> compare SHA-256 with what BigQuery already holds  -> NEW | CHANGED | UNCHANGED | DROPPED
     -> files, byte-for-byte the SQL Server text:
          local  <objects.export.local_dir>/<rg>/<server>/<db>/<views|procedures>/<schema>.<name>.sql
          GCS    gs://<landing bucket>/sql_objects/<rg>/<server>/<db>/<views|procedures>/<schema>.<name>.sql
          + manifest.json next to them (metadata, status, deploy order — no definitions)
     -> BigQuery <control dataset>.migration_sql_objects (MERGE) + migration_sql_object_history
     -> unlock

Nothing is executed in BigQuery: T-SQL does not run there. The rows are marked
translation_status = PENDING so the translation phase can pick them up later.
"""
import datetime
import hashlib
import json
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

from src.common import log
from src.objects.object_catalog import ObjectCatalogReader
from src.objects.object_types import FOLDERS, normalize_kinds
from src.selection.table_selector import TableSelector
from src.topology.naming import path_component

LOG = log.get("objects")


# ------------------------------------------------------------------- config --
def objects_config(settings: dict) -> dict:
    cfg = dict(settings.get("objects") or {})
    export = dict(cfg.get("export") or {})
    export.setdefault("local_dir", "output/sql_objects")
    export.setdefault("gcs", True)
    export.setdefault("gcs_prefix", "sql_objects")
    cfg["export"] = export
    cfg.setdefault("bigquery", True)
    cfg.setdefault("mark_dropped", True)
    cfg.setdefault("upload_parallel", 8)
    cfg["types"] = normalize_kinds(cfg.get("types") or [], default=["view", "procedure"])
    return cfg


def job_kinds(settings: dict, job) -> list[str]:
    """The kinds a job exports: its own `objects` list, else settings objects.types."""
    default = objects_config(settings)["types"]
    return normalize_kinds(getattr(job, "objects", None) or [], default=default)


def _label(settings: dict) -> str:
    s = settings.get("source") or {}
    return f"{s.get('resource_group')}/{s.get('server')}/{s.get('database')}"


def _schemas(settings: dict, job) -> list[str] | None:
    src = settings.get("source") or {}
    configured = src.get("schemas") if src.get("schemas") not in ("*", ["*"]) else None
    return list(job.schemas or []) or (list(configured) if configured else None)


def object_selector(job) -> TableSelector:
    return TableSelector(job.object_names, job.exclude_objects, job.schemas)


def is_full_scope(settings: dict, job) -> bool:
    """True when the job covers every object of its kinds in the database —
    only then may objects missing from the source be marked DROPPED."""
    src = settings.get("source") or {}
    configured = src.get("schemas") if src.get("schemas") not in ("*", ["*"]) else None
    return not (job.object_names or job.exclude_objects or job.schemas or configured)


# ----------------------------------------------------------------- planning --
def plan_objects(settings: dict, job, selector: TableSelector | None = None,
                 reader: ObjectCatalogReader | None = None) -> list[dict]:
    src = settings.get("source") or {}
    selector = selector or object_selector(job)
    if not selector.applies_to_database(src.get("resource_group"), src.get("server"), src.get("database")):
        return []
    kinds = job_kinds(settings, job)
    index = (reader or ObjectCatalogReader(settings)).read_index(kinds, _schemas(settings, job))
    by_key = {(o["schema"], o["name"]): o for o in index}
    picked = selector.select(src.get("resource_group"), src.get("server"), src.get("database"), list(by_key))
    return [by_key[k] for k in picked]


# --------------------------------------------------------------- dependencies --
def dependency_levels(objects: list[dict], database: str) -> dict[tuple, int]:
    """(schema.lower(), name.lower()) -> level. 0 = references only tables (or nothing
    in this set); n = references something of level n-1. Cycles (procedures that call
    each other) are cut, so every object gets a level."""
    ids = {(str(o["schema"]).lower(), str(o["name"]).lower()) for o in objects}
    db = str(database or "").lower()
    edges: dict[tuple, set] = {}
    for o in objects:
        me = (str(o["schema"]).lower(), str(o["name"]).lower())
        out = set()
        for r in o.get("references") or []:
            if r.get("server") or (r.get("database") and str(r["database"]).lower() != db):
                continue
            ref = (str(r.get("schema") or o["schema"]).lower(), str(r.get("name") or "").lower())
            if ref in ids and ref != me:
                out.add(ref)
        edges[me] = out
    level: dict[tuple, int] = {}
    visiting: set = set()

    def visit(node) -> int:
        if node in level:
            return level[node]
        if node in visiting:          # cycle: don't count this edge
            return -1
        visiting.add(node)
        lv = 1 + max((visit(n) for n in edges.get(node, ())), default=-1)
        visiting.discard(node)
        level[node] = max(lv, 0)
        return level[node]

    for node in sorted(edges):
        visit(node)
    return level


def _cross_db(o: dict, database: str) -> bool:
    db = str(database or "").lower()
    return any(r.get("server") or (r.get("database") and str(r["database"]).lower() != db)
               for r in o.get("references") or [])


# -------------------------------------------------------------------- files --
def object_rel_path(settings: dict, o: dict) -> str:
    """<rg>/<server>/<db>/<views|procedures>/<schema>.<name>.sql"""
    prefix = (settings.get("gcp") or {}).get("object_prefix")
    if not prefix:
        raise RuntimeError("gcp.object_prefix is not resolved — build settings with settings_for_target().")
    return f"{prefix.strip('/')}/{FOLDERS[o['kind']]}/{path_component(o['schema'])}.{path_component(o['name'])}.sql"


def _write_local(base: str, rel: str, text: str) -> str:
    path = os.path.join(base, *rel.split("/"))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:      # newline="": keep CRLF exactly as stored
        f.write(text)
    return path


def _sha(text: str | None) -> str | None:
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text is not None else None


def _js(value) -> str | None:
    return json.dumps(value, default=str, ensure_ascii=False) if value not in (None, [], {}) else None


# ------------------------------------------------------------------- running --
def run_objects(settings: dict, logging_config: dict, job, run_id: str, objects: list[dict] | None = None,
                ignore_locks: bool = False, reader=None, store=None, gcs=None, control=None,
                lock=None, metadata=None) -> dict:
    """Raw export of the job's objects in one database. One failing database never
    raises — the result carries status FAILED and the error."""
    label = _label(settings)
    L = log.for_table("objects", label)
    started = datetime.datetime.now(datetime.timezone.utc)
    try:
        return _run(settings, logging_config, job, run_id, objects, ignore_locks, reader, store, gcs, control,
                    lock, metadata, label, L, started)
    except Exception as e:
        L.error("FAILED: %s", e)
        return {"database": label, "status": "FAILED", "error": str(e), "objects": 0}


def _run(settings, logging_config, job, run_id, objects, ignore_locks, reader, store, gcs, control, lock,
         metadata, label, L, started) -> dict:
    cfg = objects_config(settings)
    export = cfg["export"]
    kinds = job_kinds(settings, job)
    to_bq = bool(cfg["bigquery"]) and getattr(job, "stage", "bq") != "gcs"
    to_gcs = bool(export.get("gcs"))
    local_dir = export.get("local_dir")
    full_scope = is_full_scope(settings, job) and bool(cfg.get("mark_dropped", True))
    src = settings.get("source") or {}

    reader = reader or ObjectCatalogReader(settings)
    if objects is None:
        objects = plan_objects(settings, job, reader=reader)
    if not objects and not (to_bq and full_scope):
        L.info("No %s selected", "/".join(FOLDERS[k] for k in kinds))
        return {"database": label, "status": "SKIPPED", "reason": "no objects selected", "objects": 0}

    if to_gcs and gcs is None:
        from src.gcs.gcs_manager import GcsManager
        gcs = GcsManager(settings)
    if to_gcs:
        gcs.ensure_bucket()
    if to_bq:
        from src.bigquery.bq_control_tables import BqControlTables
        from src.objects.object_store import ObjectStore
        control = control or BqControlTables(settings)
        control.ensure_all()
        store = store or ObjectStore(settings)
        store.ensure_tables(control)
    if metadata is None and to_bq:
        try:
            from src.metadata.metadata_manager import MetadataManager
            metadata = MetadataManager(logging_config, settings=settings)
        except Exception as e:      # logging must never stop the export
            L.warning("Pipeline logging disabled: %s", e)
    if lock is None and to_bq:
        from src.metadata.lock_manager import LockManager
        lock = LockManager(settings, owner=f"{run_id}#{uuid.uuid4().hex[:8]}")
        if ignore_locks:
            lock.enabled = False
    lock_key = f"objects:{label}".lower()

    if lock is not None:
        lock.acquire(lock_key)
    try:
        if metadata:
            metadata.log_stage_start(run_id, "__sql_objects__", "sql_objects")
        L.info("Reading %d object definition(s) (%s)", len(objects), ", ".join(FOLDERS[k] for k in kinds))
        details = reader.read_details(objects)
        levels = dependency_levels(details, src.get("database"))
        existing = store.existing() if to_bq else {}
        now = datetime.datetime.now(datetime.timezone.utc).isoformat()
        prefix = export.get("gcs_prefix") or "sql_objects"
        keys = _source_keys(settings)

        rows, history, manifest, uploads = [], [], [], []
        counts = {"NEW": 0, "CHANGED": 0, "UNCHANGED": 0, "DROPPED": 0}
        by_kind: dict[str, int] = {}
        problems = {"ENCRYPTED": [], "NO_PERMISSION": []}
        seen = set()
        for o in sorted(details, key=lambda x: (levels.get((str(x["schema"]).lower(), str(x["name"]).lower()), 0),
                                                x["kind"], x["schema"].lower(), x["name"].lower())):
            key = (o["kind"], str(o["schema"]).lower(), str(o["name"]).lower())
            seen.add(key)
            by_kind[o["kind"]] = by_kind.get(o["kind"], 0) + 1
            definition = o.get("definition")
            sha = _sha(definition)
            prev = existing.get(key)
            if not to_bq:
                change = "EXPORTED"
            elif prev is None:
                change = "NEW"
            elif prev.get("sha256") == sha:
                change = "UNCHANGED"
            else:
                change = "CHANGED"
            counts[change] = counts.get(change, 0) + 1
            if o["definition_status"] != "OK":
                problems[o["definition_status"]].append(f"{o['schema']}.{o['name']}")
            rel = object_rel_path(settings, o)
            gcs_uri = None
            if definition is not None:
                if local_dir:
                    _write_local(local_dir, rel, definition)
                need_upload = to_gcs and (change in ("NEW", "CHANGED", "EXPORTED") or getattr(job, "force", False)
                                          or not (prev or {}).get("gcs_uri"))
                if need_upload:
                    gcs_uri = f"gs://{settings['gcp']['gcs_bucket']}/{prefix}/{rel}"
                    uploads.append((f"{prefix}/{rel}", definition))
            level = levels.get((str(o["schema"]).lower(), str(o["name"]).lower()), 0)
            row = {
                **keys,
                "object_schema": o["schema"], "object_name": o["name"], "object_type": o["kind"],
                "sql_type_code": o["type_code"], "sql_type_desc": o.get("type_desc"),
                "parent_object": o.get("parent_object"),
                "definition": definition, "definition_sha256": sha,
                "definition_length": len(definition) if definition is not None else None,
                "definition_status": o["definition_status"],
                "uses_ansi_nulls": o.get("uses_ansi_nulls"), "uses_quoted_identifier": o.get("uses_quoted_identifier"),
                "is_schema_bound": o.get("is_schema_bound"),
                "parameters_json": _js(o.get("parameters")), "returns_json": _js(o.get("returns")),
                "columns_json": _js(o.get("columns")), "references_json": _js(o.get("references")),
                "references_cross_database": _cross_db(o, src.get("database")), "dependency_level": level,
                "source_created_at": o.get("created_at"), "source_modified_at": o.get("modified_at"),
                "gcs_uri": gcs_uri, "change_status": change,
                "translation_status": "PENDING" if definition is not None else "NOT_AVAILABLE",
                "extracted_at": now, "run_id": run_id,
            }
            rows.append(row)
            if change in ("NEW", "CHANGED"):
                history.append({k: row[k] for k in (*keys, "object_schema", "object_name", "object_type",
                                                     "change_status", "definition", "definition_sha256",
                                                     "definition_status", "source_modified_at", "gcs_uri",
                                                     "extracted_at", "run_id")})
            manifest.append({
                "schema": o["schema"], "name": o["name"], "type": o["kind"], "sql_type": o.get("type_desc"),
                "file": rel.split("/", 3)[-1] if definition is not None else None,
                "status": o["definition_status"], "change": change, "dependency_level": level,
                "sha256": sha, "modified_at": o.get("modified_at"), "parent_object": o.get("parent_object"),
                "parameters": [p["name"] for p in o.get("parameters") or []],
                "references": [".".join(x for x in (r.get("database"), r.get("schema"), r.get("name")) if x)
                               for r in o.get("references") or []],
                "references_cross_database": row["references_cross_database"],
            })

        dropped = []
        if to_bq and full_scope:
            for key, prev in existing.items():
                if key[0] in kinds and key not in seen and prev.get("source_status") == "PRESENT":
                    dropped.append(prev | {"kind": key[0]})
                    history.append({**keys, "object_schema": prev.get("schema"), "object_name": prev.get("name"),
                                    "object_type": key[0], "change_status": "DROPPED", "definition": None,
                                    "definition_sha256": None, "definition_status": None,
                                    "source_modified_at": None, "gcs_uri": prev.get("gcs_uri"),
                                    "extracted_at": now, "run_id": run_id})
            counts["DROPPED"] = len(dropped)

        if uploads:
            L.info("Uploading %d definition file(s) to gs://%s/%s/", len(uploads), settings["gcp"]["gcs_bucket"],
                   prefix)
            with ThreadPoolExecutor(max_workers=max(1, int(cfg.get("upload_parallel") or 8)),
                                    thread_name_prefix="sqlobj") as pool:
                list(pool.map(lambda u: gcs.upload_text(u[0], u[1], "application/sql; charset=utf-8"), uploads))

        manifest_doc = {
            "source": {"resource_group": src.get("resource_group"), "server": src.get("server"),
                       "database": src.get("database"), "azure_location": src.get("azure_location")},
            "target": {"gcp_project": settings["gcp"].get("project_id"), "dataset": settings["gcp"].get("dataset"),
                       "objects_table": getattr(store, "objects_ref", None)},
            "run_id": run_id, "extracted_at": now, "kinds": kinds, "translated": False,
            "counts": {**counts, **{f"{k}_count": v for k, v in by_kind.items()}},
            "dropped": [f"{d['kind']}:{d.get('schema')}.{d.get('name')}" for d in dropped],
            "objects": manifest,           # in deploy order: dependency_level, then type, schema, name
        }
        manifest_text = json.dumps(manifest_doc, indent=2, default=str, ensure_ascii=False)
        manifest_rel = f"{settings['gcp']['object_prefix'].strip('/')}/manifest.json"
        local_root = None
        if local_dir:
            _write_local(local_dir, manifest_rel, manifest_text)
            local_root = os.path.join(local_dir, *settings["gcp"]["object_prefix"].strip("/").split("/"))
        gcs_root = None
        if to_gcs:
            gcs.upload_text(f"{prefix}/{manifest_rel}", manifest_text, "application/json")
            gcs_root = f"gs://{settings['gcp']['gcs_bucket']}/{prefix}/{settings['gcp']['object_prefix'].strip('/')}/"

        if to_bq:
            store.write(rows, history, kinds, mark_dropped=full_scope, run_id=run_id)

        for status, names in problems.items():
            if names:
                hint = ("created WITH ENCRYPTION — SQL Server does not return their text" if status == "ENCRYPTED"
                        else "the login lacks VIEW DEFINITION (GRANT VIEW DEFINITION TO <login>)")
                L.warning("%d object(s) without definition (%s): %s", len(names), hint,
                          ", ".join(names[:10]) + (" ..." if len(names) > 10 else ""))
        result = {
            "database": label, "status": "SUCCESS", "objects": len(rows), "by_kind": by_kind,
            "new": counts.get("NEW", 0), "changed": counts.get("CHANGED", 0),
            "unchanged": counts.get("UNCHANGED", 0), "dropped": counts.get("DROPPED", 0),
            "exported": counts.get("EXPORTED", 0), "uploaded": len(uploads),
            "encrypted": len(problems["ENCRYPTED"]), "no_permission": len(problems["NO_PERMISSION"]),
            "local_dir": local_root, "gcs_prefix": gcs_root,
            "bigquery_table": getattr(store, "objects_ref", None) if to_bq else None,
        }
        L.info("Raw objects done: %s", ", ".join(f"{k}={result[k]}" for k in
                                                ("objects", "new", "changed", "unchanged", "dropped", "uploaded")))
        if metadata:
            metadata.log_stage_end(run_id, "__sql_objects__", "sql_objects", "SUCCESS", rows_processed=len(rows),
                                   extra={k: v for k, v in result.items() if k not in ("database",)})
        return result
    except Exception as e:
        if metadata:
            try:
                metadata.log_stage_end(run_id, "__sql_objects__", "sql_objects", "FAILED", error_message=str(e))
            except Exception:
                pass
        raise
    finally:
        if lock is not None:
            lock.release(lock_key)


def _source_keys(settings: dict) -> dict:
    from src.bigquery.bq_control_tables import source_key_values
    return source_key_values(settings)
