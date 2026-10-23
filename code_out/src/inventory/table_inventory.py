"""
table_inventory.py

Answers "which SQL server holds table X?" so `python3 main.py --table customer`
works without --region.

How it works
  * refresh_inventory() connects to every configured region's SQL server (through the
    VPN tunnel, same as a migration), lists every base table via INFORMATION_SCHEMA and
    stores  region -> host/database -> [schema.table, ...]  in a local JSON file
    (inventory.file, default config/table_inventory.json).
  * The same rows are mirrored best-effort to a BigQuery table (inventory.bq_dataset.table_inventory)
    so people can query "where does table X live" in SQL. The JSON file is what the CLI reads;
    a BigQuery problem never stops a run.
  * find_table() / locate_regions() look a table up in that inventory. If the table is not in
    it (new table, or the inventory has never been built) the inventory is refreshed once
    automatically (inventory.auto_refresh_on_miss, default true) and looked up again.

A server that cannot be reached during a refresh keeps its previous entries and is marked
ERROR, so one down server never wipes what is known about it. If a table is "not found" while
some server could not be scanned, the error says so (the table may simply live there).

settings.yaml (all optional):
    inventory:
      file: config/table_inventory.json
      auto_refresh_on_miss: true
      max_age_hours: 168          # warn (never fail) when the inventory is older than this
      parallel_servers: 4         # how many SQL servers are scanned at once
      bigquery_sync: true         # mirror to BigQuery (best effort)
      bq_dataset: migration_inventory
      bq_location: US
"""
import datetime
import json
import os
import tempfile
from concurrent.futures import ThreadPoolExecutor

from src.config.region_resolver import (
    _entry,
    _host_problem,
    available_regions,
    is_all,
    regions_to_run,
    resolve_region,
)

DEFAULT_FILE = "config/table_inventory.json"
INVENTORY_TABLE = "table_inventory"


class InventoryError(RuntimeError):
    """The inventory could not answer the question (message says what to do)."""


def _cfg(settings: dict) -> dict:
    cfg = settings.get("inventory") or {}
    return cfg if isinstance(cfg, dict) else {}


def inventory_path(settings: dict) -> str:
    return str(_cfg(settings).get("file") or DEFAULT_FILE)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---- storage -----------------------------------------------------------------------------
def empty_inventory() -> dict:
    return {"version": 1, "generated_at": None, "regions": {}}


def load_inventory(path: str) -> dict:
    if not os.path.exists(path):
        return empty_inventory()
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError) as e:
        raise InventoryError(f"Could not read the table inventory {path}: {e}. Delete it and run "
                             "`python3 main.py --refresh-inventory`.") from e
    if not isinstance(data, dict) or not isinstance(data.get("regions"), dict):
        raise InventoryError(f"{path} is not a valid table inventory. Delete it and run "
                             "`python3 main.py --refresh-inventory`.")
    return data


def save_inventory(path: str, inventory: dict) -> None:
    """Atomic write: a crash mid-write can never leave a half-written inventory."""
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".inventory-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(inventory, f, indent=1, sort_keys=True)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def age_hours(inventory: dict) -> float | None:
    stamp = inventory.get("generated_at")
    if not stamp:
        return None
    try:
        then = datetime.datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except ValueError:
        return None
    return (datetime.datetime.now(datetime.timezone.utc) - then).total_seconds() / 3600


# ---- scanning ----------------------------------------------------------------------------
def _default_discovery(resolved_settings):
    from src.discovery.table_discovery import TableDiscovery
    return TableDiscovery(resolved_settings)


def scan_region(settings: dict, region: str, discovery_factory=_default_discovery) -> dict:
    """Lists every table on ONE region's SQL server. Returns that region's inventory entry;
    never raises for a server problem (status ERROR + message instead)."""
    entry = {"host": None, "database": None, "scanned_at": _now(), "status": "OK", "error": None, "tables": []}
    discovery = None
    try:
        resolved = resolve_region(settings, region)
        src = resolved["source"]
        entry["host"], entry["database"] = src.get("host"), src.get("database")
        discovery = discovery_factory(resolved)
        for schema in discovery.list_schemas():
            for name in discovery.list_tables(schema):
                entry["tables"].append({"schema": schema, "name": name})
        entry["tables"].sort(key=lambda t: (t["schema"].lower(), t["name"].lower()))
    except Exception as e:          # unreachable server, bad login, DNS... — recorded, not raised
        entry["status"] = "ERROR"
        entry["error"] = " ".join(str(e).split())[:300]
    finally:
        engine = getattr(discovery, "_engine", None)
        if engine is not None:
            try:
                engine.dispose()
            except Exception:
                pass
    return entry


def refresh_inventory(settings: dict, requested=None, discovery_factory=_default_discovery,
                      save: bool = True, sync_bq: bool | None = None) -> dict:
    """Scans the requested region(s) (default: every configured region with a host) and merges
    the result into the saved inventory. A full refresh also drops regions that are no longer
    in settings.yaml. Returns the merged inventory."""
    path = inventory_path(settings)
    previous = load_inventory(path)
    targets = regions_to_run(settings, requested if requested else "all")
    if targets == [None]:
        raise InventoryError("No `regions:` configured in config/settings.yaml — nothing to inventory.")
    workers = max(1, min(int(_cfg(settings).get("parallel_servers", 4) or 4), len(targets)))

    print(f"[inventory] Scanning {len(targets)} SQL server(s): {', '.join(targets)} ...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(lambda r: (r, scan_region(settings, r, discovery_factory)), targets))

    merged = {"version": 1, "generated_at": _now(), "regions": dict(previous.get("regions", {}))}
    for region, entry in results:
        old = merged["regions"].get(region)
        if entry["status"] == "ERROR" and old:
            entry["tables"] = old.get("tables", [])            # keep what we knew
            entry["scanned_at"] = old.get("scanned_at")
        merged["regions"][region] = entry
        if entry["status"] == "OK":
            print(f"[inventory]   {region:24s} {len(entry['tables']):6d} table(s)  ({entry['host']})")
        else:
            print(f"[inventory]   {region:24s} ERROR: {entry['error']}")

    if is_all(requested) or not requested:                     # full refresh: forget removed regions
        configured = {str(r) for r in available_regions(settings)}
        for gone in [r for r in merged["regions"] if r not in configured]:
            del merged["regions"][gone]

    if save:
        save_inventory(path, merged)
        print(f"[inventory] Saved {path}")
    if sync_bq is None:
        sync_bq = bool(_cfg(settings).get("bigquery_sync", True))
    if save and sync_bq:
        sync_to_bigquery(settings, merged)
    return merged


# ---- lookup ------------------------------------------------------------------------------
def find_table(inventory: dict, table: str, schema: str | None = None) -> list[dict]:
    """Every (region, schema, name) whose table name matches, case-insensitively."""
    out = []
    for region, entry in inventory.get("regions", {}).items():
        for t in entry.get("tables", []):
            if t["name"].lower() != table.lower():
                continue
            if schema and t["schema"].lower() != schema.lower():
                continue
            out.append({"region": region, "schema": t["schema"], "name": t["name"],
                        "host": entry.get("host")})
    return out


def _unscanned(inventory: dict) -> list[str]:
    return [r for r, e in inventory.get("regions", {}).items() if e.get("status") != "OK"]


def locate(settings: dict, table: str, schema: str | None = None, refresh=None,
           auto_refresh: bool | None = None) -> tuple[list[dict], dict]:
    """(hits, inventory) for a table. Refreshes the inventory once if the table is not in it."""
    refresh = refresh or refresh_inventory
    path = inventory_path(settings)
    inventory = load_inventory(path)
    if auto_refresh is None:
        auto_refresh = bool(_cfg(settings).get("auto_refresh_on_miss", True))

    hits = find_table(inventory, table, schema)
    if not hits and auto_refresh:
        why = "has not been built yet" if not inventory.get("regions") else f'does not list "{table}"'
        print(f"[inventory] The table inventory {why} — scanning all SQL servers once ...")
        inventory = refresh(settings)
        hits = find_table(inventory, table, schema)
    elif hits:
        limit = _cfg(settings).get("max_age_hours", 168)
        age = age_hours(inventory)
        if limit and age is not None and age > float(limit):
            print(f"[inventory] WARNING: the inventory is {age / 24:.1f} day(s) old — run "
                  "`python3 main.py --refresh-inventory` if tables were added or moved.")
    return hits, inventory


def locate_regions(settings: dict, table: str, schema: str | None = None, refresh=None,
                   auto_refresh: bool | None = None) -> dict:
    """{region: {"schema": s | None, "name": canonical_name}} for every region holding the table.
    `schema` is set only when the region has exactly one matching schema."""
    hits, inventory = locate(settings, table, schema, refresh=refresh, auto_refresh=auto_refresh)
    usable = {str(r) for r in available_regions(settings)
              if not _host_problem(_entry(settings, r).get("host"))}
    by_region: dict = {}
    for h in hits:
        if h["region"] in usable:
            by_region.setdefault(h["region"], []).append(h)
    if not by_region:
        label = f"{schema}.{table}" if schema else table
        broken = _unscanned(inventory)
        extra = (f" These server(s) could not be scanned, so the table may be there: {', '.join(broken)}."
                 if broken else "")
        raise InventoryError(
            f'Table "{label}" was not found on any configured SQL server.{extra} Check the spelling, '
            "or give --region to look on one specific server.")
    result = {}
    for region, items in by_region.items():
        schemas = sorted({i["schema"] for i in items})
        result[region] = {"name": items[0]["name"], "schema": schemas[0] if len(schemas) == 1 else None}
    return result


# ---- BigQuery mirror ---------------------------------------------------------------------
def inventory_rows(inventory: dict) -> list[dict]:
    rows = []
    for region, e in inventory.get("regions", {}).items():
        for t in e.get("tables", []):
            rows.append({"region": region, "host": e.get("host"), "database_name": e.get("database"),
                         "schema_name": t["schema"], "table_name": t["name"],
                         "scanned_at": e.get("scanned_at"), "scan_status": e.get("status")})
    return rows


def sync_to_bigquery(settings: dict, inventory: dict, client=None) -> bool:
    """Replaces <project>.<inventory.bq_dataset>.table_inventory with the current inventory.
    Best effort: returns False (and warns) instead of raising."""
    cfg = _cfg(settings)
    try:
        from google.cloud import bigquery
        project = settings["gcp"]["project_id"]
        dataset = str(cfg.get("bq_dataset") or "migration_inventory")
        location = str(cfg.get("bq_location") or "US")
        client = client or bigquery.Client(project=project, location=location)
        ds = bigquery.Dataset(f"{project}.{dataset}")
        ds.location = location
        client.create_dataset(ds, exists_ok=True)
        schema = [bigquery.SchemaField(n, t) for n, t in (
            ("region", "STRING"), ("host", "STRING"), ("database_name", "STRING"),
            ("schema_name", "STRING"), ("table_name", "STRING"),
            ("scanned_at", "TIMESTAMP"), ("scan_status", "STRING"))]
        rows = inventory_rows(inventory)
        job = client.load_table_from_json(
            rows, f"{project}.{dataset}.{INVENTORY_TABLE}",
            job_config=bigquery.LoadJobConfig(schema=schema,
                                              write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE))
        job.result()
        print(f"[inventory] BigQuery table {project}.{dataset}.{INVENTORY_TABLE} updated ({len(rows)} row(s))")
        return True
    except Exception as e:
        print(f"[inventory] WARNING: BigQuery mirror skipped: {' '.join(str(e).split())[:200]}")
        return False
