"""
paths.py — file/object paths for one table run.

Every path carries <resource_group>/<server>/<database> (gcp.object_prefix), the
schema and table, the run scope (full / incremental / partial-<hash>) and a
chunk folder that is UNIQUE per attempt:

  local   data/parquet/<prefix>/<schema>/<table>/<scope>/<chunk>/part_00000.parquet
  azure   <container>/parquet/<prefix>/<schema>/<table>/<scope>/<chunk>/part_00000.parquet
  gcs     gs://<bucket>/parquet/<prefix>/<schema>/<table>/<scope>/<chunk>/part_00000.parquet

A chunk is loaded into BigQuery with ONE load job over gs://.../<chunk>/*.parquet.
Because the chunk folder name includes an attempt id, a retried chunk never
picks up stale files left by a failed attempt.
"""
import os
import uuid

from src.topology.naming import path_component, slug


def _prefix(settings: dict) -> str:
    prefix = (settings.get("gcp") or {}).get("object_prefix")
    if not prefix:
        raise RuntimeError("gcp.object_prefix is not resolved — build settings with "
                           "src/topology/targets.settings_for_target(settings, target).")
    return prefix.strip("/")


def scope_component(scope_key: str) -> str:
    return slug(scope_key, "-") or "full"


def chunk_tag(chunk_index: int) -> str:
    return f"chunk_{chunk_index:05d}_{uuid.uuid4().hex[:8]}"


def table_rel_dir(settings: dict, schema: str, table: str, scope_key: str) -> str:
    return (f"parquet/{_prefix(settings)}/{path_component(schema)}/{path_component(table)}/"
            f"{scope_component(scope_key)}/")


def chunk_rel_dir(settings: dict, schema: str, table: str, scope_key: str, tag: str) -> str:
    return f"{table_rel_dir(settings, schema, table, scope_key)}{tag}/"


def batch_filename(batch_index: int) -> str:
    return f"part_{batch_index:05d}.parquet"


def local_dir(settings: dict, rel_dir: str) -> str:
    base = (settings.get("extraction") or {}).get("local_dir") or "data"
    return os.path.join(base, *rel_dir.strip("/").split("/"))
