"""
paths.py — file/object paths for one table run.

Every path carries <resource_group>/<server>/<database> (gcp.object_prefix), the
schema and table, the run scope (full / incremental / partial-<hash>) and a
run folder that is UNIQUE per run:

  local   data/parquet/<prefix>/<schema>/<table>/<scope>/<run>/<file>
  azure   <container>/parquet/<prefix>/<schema>/<table>/<scope>/<run>/<file>
  gcs     gs://<bucket>/parquet/<prefix>/<schema>/<table>/<scope>/<run>/<file>

  <run> = run_<UTC yyyymmddThhmmssZ>_<8 hex>, e.g. run_20261022T074512Z_1a2b3c4d

All Parquet files of one table run go into its run folder and are loaded into
BigQuery with ONE load job over gs://.../<run>/*.parquet. Because every run has
its own folder, a retried run never picks up files left by a failed one.

File names (extraction.file_naming)
-----------------------------------
descriptive (default) — the file describes itself even after it has been copied
out of its folder, from the broadest level to the narrowest:

  rg-<rg>__srv-<server>__db-<database>__sch-<schema>__tbl-<table>__mode-<scope>__run-20261022T074512Z__part-00007.parquet

  * fields are separated by "__" and every field is "<key>-<value>"
  * a value never contains "__" (runs of unsafe characters collapse to one "_"),
    so parse_filename() splits a name back into its fields without guessing,
    even when a table is called order__line_items or Order Details
  * when a value had to be changed (unsafe characters, leading "_", too long) a
    4-hex hash of the ORIGINAL name is added ("Order Details" -> Order_Details-abb7),
    so two different source names never produce the same file name
  * part numbers are zero-padded (part-00007) so names sort correctly
  * run-<UTC time> is when the table run started, so a file copied out of its
    folder still says which run produced it
  * the whole name is kept <= 200 bytes (Linux/macOS allow 255 per file name);
    long values are shortened (32, then 20, then 12 characters) + hash

  The file name is for people and tooling; the exact original names (case,
  brackets, spaces) remain in the control tables — never parse names to recover them.

short — the previous name, part_00000.parquet.
"""
import datetime
import hashlib
import os
import re
import uuid

from src.topology.naming import path_component, slug

FIELD_SEP = "__"
FILE_FIELDS = ("rg", "srv", "db", "sch", "tbl", "mode", "run", "part")
MAX_FILENAME_BYTES = 200
_SHORT_VALUES = (32, 20, 12)      # tried in turn until the name fits
_RUN_TAG_RE = re.compile(r"^run_(\d{8}T\d{6}Z)_([0-9a-f]+)$")


def _prefix(settings: dict) -> str:
    prefix = (settings.get("gcp") or {}).get("object_prefix")
    if not prefix:
        raise RuntimeError("gcp.object_prefix is not resolved — build settings with "
                           "src/topology/targets.settings_for_target(settings, target).")
    return prefix.strip("/")


def scope_component(scope_key: str) -> str:
    return slug(scope_key, "-") or "full"


def run_tag(now: datetime.datetime | None = None) -> str:
    """Folder name of one table run: run_20261022T074512Z_1a2b3c4d."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    return f"run_{now.strftime('%Y%m%dT%H%M%SZ')}_{uuid.uuid4().hex[:8]}"


def parse_run_tag(tag: str) -> tuple[str, str]:
    """'run_20261022T074512Z_1a2b3c4d' -> ('20261022T074512Z', '1a2b3c4d')."""
    m = _RUN_TAG_RE.match(str(tag or ""))
    if not m:
        raise ValueError(f"not a run tag: {tag!r}")
    return m.group(1), m.group(2)


def table_rel_dir(settings: dict, schema: str, table: str, scope_key: str) -> str:
    return (f"parquet/{_prefix(settings)}/{path_component(schema)}/{path_component(table)}/"
            f"{scope_component(scope_key)}/")


def run_rel_dir(settings: dict, schema: str, table: str, scope_key: str, tag: str) -> str:
    return f"{table_rel_dir(settings, schema, table, scope_key)}{tag}/"


# ------------------------------------------------------------- file names --
def _hash4(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:4]


def file_component(text, max_len: int | None = None) -> str:
    """One field value for a descriptive file name: letters, digits, '-' and single
    '_' only (never '__'); a hash of the original is added whenever it was changed."""
    raw = str(text if text is not None else "")
    safe = re.sub(r"[^A-Za-z0-9-]+", "_", raw).strip("_-") or "x"
    changed = safe != raw
    if max_len and len(safe) > max_len:
        safe, changed = safe[:max_len].rstrip("_-") or "x", True
    return f"{safe}-{_hash4(raw)}" if changed else safe


def descriptive_filename(resource_group: str, server: str, database: str, schema: str, table: str,
                         scope_key: str, run_time: str, part_index: int) -> str:
    def build(max_len):
        values = {
            "rg": file_component(resource_group, max_len),
            "srv": file_component(str(server or "").split(".")[0], max_len),
            "db": file_component(database, max_len),
            "sch": file_component(schema, max_len),
            "tbl": file_component(table, max_len),
            "mode": scope_component(scope_key),
            "run": file_component(run_time),
            "part": f"{int(part_index):05d}",
        }
        return FIELD_SEP.join(f"{k}-{values[k]}" for k in FILE_FIELDS) + ".parquet"

    for max_len in (None, *_SHORT_VALUES):
        name = build(max_len)
        if len(name.encode("utf-8")) <= MAX_FILENAME_BYTES:
            break
    return name


def parse_filename(name: str) -> dict:
    """Inverse of descriptive_filename for the sanitized values:
    {'rg': ..., 'srv': ..., 'db': ..., 'sch': ..., 'tbl': ..., 'mode': ...,
     'run': '20261022T074512Z', 'part': 7}."""
    base = os.path.basename(str(name))
    if not base.endswith(".parquet"):
        raise ValueError(f"not a Parquet file name: {name!r}")
    fields = base[: -len(".parquet")].split(FIELD_SEP)
    out = {}
    for field in fields:
        key, sep, value = field.partition("-")
        if not sep or key not in FILE_FIELDS or key in out:
            raise ValueError(f"unexpected field {field!r} in {base!r}")
        out[key] = value
    missing = [k for k in FILE_FIELDS if k not in out]
    if missing:
        raise ValueError(f"{base!r} is missing field(s) {missing}")
    out["part"] = int(out["part"])
    return out


_PART_RE = re.compile(r"part[-_](\d+)\.parquet$")


def part_index(name: str) -> int | None:
    """Part number of a Parquet file name (descriptive or short), None if it isn't one."""
    m = _PART_RE.search(str(name or ""))
    return int(m.group(1)) if m else None


def tag_of(rel_dir: str) -> str:
    """parquet/.../<scope>/run_<UTC>_<id>/ -> run_<UTC>_<id>"""
    return str(rel_dir).rstrip("/").rsplit("/", 1)[-1]


def batch_filename(batch_index: int, settings: dict | None = None, schema: str | None = None,
                   table: str | None = None, scope_key: str | None = None, tag: str | None = None) -> str:
    """Name of one Parquet file in a run folder. With settings + table + run tag
    and extraction.file_naming = descriptive (default) the name describes the file;
    otherwise the short part_00000.parquet."""
    naming = str(((settings or {}).get("extraction") or {}).get("file_naming") or "descriptive").lower()
    if naming not in ("descriptive", "short"):
        raise ValueError(f'extraction.file_naming "{naming}" — use descriptive or short.')
    if settings is None or naming == "short" or tag is None:
        return f"part_{batch_index:05d}.parquet"
    src = settings.get("source") or {}
    run_time, _ = parse_run_tag(tag)
    return descriptive_filename(src.get("resource_group"), src.get("server"), src.get("database"),
                                schema, table, scope_key, run_time, batch_index)


def local_dir(settings: dict, rel_dir: str) -> str:
    base = (settings.get("extraction") or {}).get("local_dir") or "data"
    return os.path.join(base, *rel_dir.strip("/").split("/"))
