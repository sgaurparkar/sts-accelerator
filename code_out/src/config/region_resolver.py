"""
region_resolver.py

Maps an Azure region name (exactly as Azure displays it, e.g.
"North Central US", "Brazil South", "Southeast Asia") to the Azure SQL
server in that region, and returns a COPY of the settings with `source`
pointed at that server and the GCP side separated per region:

    landing bucket   <gcp.gcs_bucket>-<region>      migration-landing-bucket-east-us
    control dataset  <gcp.bq_dataset>_<region>      migrated_data_east_us
    data datasets    <region>_<schema>              east_us_dbo   (see dataset_naming.py)
                     (multi-project mode instead: ONE dataset <server>_<database>, tables Orders / <schema>__Orders)

all created in regions.<name>.gcp_location (the GCP region matching the
Azure one). Every other module already reads host/bucket/dataset from
settings, so none of them need to know how the region was chosen.

Matching is forgiving: case, spaces, hyphens and underscores are ignored, so
"North Central US", "north-central-us" and "northcentralus" are the same.
No prefix/fuzzy matching — "West US" never silently resolves to "West US 2".

Every problem (unknown region, placeholder host, bad location, name clashes,
...) raises RegionError with a one-line, actionable message.
"""
import copy
import hashlib
import os
import re


class RegionError(RuntimeError):
    """Bad/missing region or region configuration."""


_ALL_TOKENS = {"all", "*"}
# us-east4, southamerica-east1, ... plus the BigQuery/GCS multi-regions.
_LOCATION_RE = re.compile(r"^(?:[a-z]+-[a-z]+[0-9]+|us|eu|asia|nam4|eur4|eur5|eur6|asia1)$", re.I)
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")


def _normalize(name) -> str:
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def same_region(a, b) -> bool:
    return bool(a) and bool(b) and _normalize(a) == _normalize(b)


def region_slug(region_name) -> str:
    """Identifier-safe form: 'North Central US' -> 'north_central_us'."""
    return re.sub(r"[^a-z0-9]+", "_", str(region_name).lower()).strip("_")


def clean_requested(requested) -> str | None:
    """Accepts a string, a list of CLI words, or None; returns a stripped
    string or None (empty / whitespace-only counts as 'not given')."""
    if requested is None:
        return None
    if isinstance(requested, (list, tuple)):
        requested = " ".join(str(p) for p in requested)
    requested = str(requested).strip()
    return requested or None


def is_all(requested) -> bool:
    requested = clean_requested(requested)
    return bool(requested) and requested.lower() in _ALL_TOKENS


def region_from_env() -> str | None:
    """Used by the Airflow DAGs, which have no CLI: set SOURCE_REGION."""
    return clean_requested(os.environ.get("SOURCE_REGION"))


def _regions_cfg(settings: dict) -> dict:
    regions = settings.get("regions") or {}
    if not isinstance(regions, dict):
        raise RegionError("`regions:` in config/settings.yaml must be a mapping of "
                          "region name -> {host: ..., gcp_location: ...}.")
    return regions


def available_regions(settings: dict) -> list[str]:
    return [str(k) for k in _regions_cfg(settings)]


def _index(settings: dict) -> dict[str, str]:
    """normalized name -> the key as written in settings. Rejects names that
    collide after normalization or normalize to nothing."""
    index: dict[str, str] = {}
    for key in _regions_cfg(settings):
        norm = _normalize(key)
        if not norm:
            raise RegionError(f'Invalid region name "{key}" in config/settings.yaml.')
        if norm in index:
            raise RegionError(f'Regions "{index[norm]}" and "{key}" in config/settings.yaml '
                              "are the same region once case/spaces/hyphens are ignored.")
        index[norm] = key
    return index


def _entry(settings: dict, key) -> dict:
    entry = _regions_cfg(settings)[key]
    if entry is None:
        return {}
    if isinstance(entry, str):          # shorthand:  East US: myserver.database.windows.net
        return {"host": entry}
    if not isinstance(entry, dict):
        raise RegionError(f'Region "{key}" in config/settings.yaml must be a mapping '
                          "(host, gcp_location, ...).")
    return entry


def _host_problem(host) -> str | None:
    """None if `host` looks like a real server name, else why not."""
    host = str(host or "").strip()
    if not host or "<" in host or ">" in host:
        return "has no SQL server host set (still a placeholder)"
    if "://" in host or "/" in host or any(c.isspace() for c in host):
        return f'has an invalid host "{host}" — use just the server name, e.g. myserver.database.windows.net'
    return None


def _canonical(settings: dict, requested: str) -> str:
    index = _index(settings)
    key = index.get(_normalize(requested))
    if key is None:
        known = ", ".join(f'"{r}"' for r in available_regions(settings))
        raise RegionError(f'Unknown region "{requested}". Choose one of: {known}, or "all".')
    return str(key)


def regions_to_run(settings: dict, requested) -> list:
    """Region names a run should cover.

      * no `regions:` configured  -> [None]  (legacy single-server mode);
                                     passing a region then is an error
      * "all"                     -> every region whose host is filled in
                                     (placeholder regions are skipped with a warning)
      * a region name             -> [that region, as written in settings]
    """
    requested = clean_requested(requested)
    index = _index(settings)
    if not index:
        if requested:
            raise RegionError(f'--region "{requested}" was given but no `regions:` are '
                              "configured in config/settings.yaml.")
        return [None]
    if not requested:
        known = ", ".join(f'"{r}"' for r in available_regions(settings))
        raise RegionError(f'--region is required. Choose one of: {known}, or "all".')
    if is_all(requested):
        usable, skipped = [], []
        for key in index.values():
            (skipped if _host_problem(_entry(settings, key).get("host")) else usable).append(str(key))
        for key in skipped:
            print(f'[region_resolver] WARNING: skipping "{key}" — no SQL server host set.')
        if not usable:
            raise RegionError("No region has a SQL server host set in config/settings.yaml.")
        return usable
    return [_canonical(settings, requested)]


# ---------------------------------------------------------------------------------------------
# Multi-project mode (gcp project per Azure resource group). Off unless
# `multi_project.enabled: true` in config/settings.yaml, so existing setups are unchanged.
# ---------------------------------------------------------------------------------------------
_PROJECT_ID_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")


def slug_hyphen(name) -> str:
    return re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-")


def _short_hash(text: str, n: int = 6) -> str:
    return hashlib.sha1(str(text).lower().encode()).hexdigest()[:n]


def multi_project_cfg(settings: dict) -> dict:
    mp = settings.get("multi_project") or {}
    return mp if isinstance(mp, dict) else {}


def multi_project_enabled(settings: dict) -> bool:
    return bool(multi_project_cfg(settings).get("enabled"))


def flat_naming(settings: dict) -> bool:
    """True (default in multi-project mode) = ONE dataset per server+database and the schema carried in
    the table name (dbo.Orders -> Orders, sales.Orders -> sales__Orders). False ("per_schema") = the
    original one dataset per schema with plain table names. Set multi_project.dataset_layout."""
    layout = str(multi_project_cfg(settings).get("dataset_layout") or "server_db").strip().lower()
    if layout not in ("server_db", "per_schema"):
        raise RegionError(f'multi_project.dataset_layout "{layout}" is not valid - use "server_db" or "per_schema".')
    return layout == "server_db"


def project_id_for(settings: dict, resource_group: str) -> str:
    """GCP project id for an Azure resource group.

    Explicit `multi_project.projects: {<resource group>: <project id>}` wins; otherwise
    <project_prefix>-<resource group slug>, shortened with a hash when over 30 characters
    (project ids are 6-30 chars, lowercase letters/digits/hyphens, start with a letter)."""
    mp = multi_project_cfg(settings)
    for rg, pid in (mp.get("projects") or {}).items():
        if _normalize(rg) == _normalize(resource_group):
            pid = str(pid).strip()
            if not _PROJECT_ID_RE.match(pid):
                raise RegionError(f'multi_project.projects."{rg}" = "{pid}" is not a valid project id '
                                  "(6-30 chars: lowercase letters, digits, hyphens; starts with a letter).")
            return pid
    # project_prefix: key absent -> "mig" (original default); present but empty ("") -> no prefix, so the
    # project id is just the resource group name (rg-na-eastus -> project rg-na-eastus).
    prefix = slug_hyphen(mp["project_prefix"] if "project_prefix" in mp and mp["project_prefix"] is not None else "mig")
    base = f"{prefix}-{slug_hyphen(resource_group)}".strip("-") if prefix else slug_hyphen(resource_group)
    if len(base) > 30:
        base = f"{base[:23].rstrip('-')}-{_short_hash(resource_group)}"
    if not _PROJECT_ID_RE.match(base):
        raise RegionError(f'Resource group "{resource_group}" gives the project id "{base}", which is not valid '
                          "(6-30 chars, lowercase letters/digits/hyphens, starting with a letter). Set "
                          f'multi_project.projects."{resource_group}" to a project id of your choice.')
    return base


def _server_of(entry: dict) -> str:
    """Server label: entry.server, else the first part of the host name."""
    explicit = str(entry.get("server") or "").strip()
    if explicit:
        return explicit
    return str(entry.get("host") or "").strip().split(".")[0]


def target_info(settings: dict, key) -> dict:
    """What one configured target (a `regions:` entry) maps to, without creating anything."""
    entry = _entry(settings, key)
    rg = str(entry.get("resource_group") or "").strip()
    server = _server_of(entry)
    db = str(entry.get("database") or (settings.get("source") or {}).get("database") or "")
    info = {"target": str(key), "resource_group": rg, "server": server, "database": db,
            "host": str(entry.get("host") or "")}
    if multi_project_enabled(settings) and rg:
        pid = project_id_for(settings, rg)
        info["project_id"] = pid
        info["dataset_prefix"] = f"{region_slug(server)}_{region_slug(db)}"
        info["flat_naming"] = flat_naming(settings)
        info["bucket"] = _multi_bucket(pid, region_slug(key), entry)
    return info


def _multi_bucket(project_id: str, slug: str, entry: dict) -> str:
    if entry.get("gcs_bucket"):
        return str(entry["gcs_bucket"]).strip()
    bucket = f"{project_id}-{slug.replace('_', '-')}"
    if len(bucket) > 63:
        bucket = f"{bucket[:56].rstrip('-')}-{_short_hash(bucket)}"
    return bucket


def select_targets(settings: dict, region=None, resource_group=None, server=None) -> list:
    """Targets (keys of `regions:`) for --region / --resource-group / --server.

    --region picks by key as before. --resource-group and --server narrow the set (or, without
    --region, pick from every usable target). 'all' for any of them means no narrowing."""
    region, resource_group, server = (clean_requested(x) for x in (region, resource_group, server))
    narrow = [x for x in (resource_group, server) if x and not is_all(x)]
    if not region and (resource_group or server):
        region = "all"
    keys = regions_to_run(settings, region)
    if keys == [None] or not narrow:
        return keys
    out = []
    for k in keys:
        entry = _entry(settings, k)
        if resource_group and not is_all(resource_group) and _normalize(entry.get("resource_group")) != _normalize(resource_group):
            continue
        if server and not is_all(server) and _normalize(_server_of(entry)) != _normalize(server):
            continue
        out.append(k)
    if not out:
        rgs = sorted({str(_entry(settings, k).get("resource_group") or "(none)") for k in keys})
        raise RegionError(f"No configured target matches resource group "
                          f"{resource_group or '*'} / server {server or '*'}. "
                          f"Resource groups in config/settings.yaml: {', '.join(rgs)}.")
    return out


def resolve_region(settings: dict, region) -> dict:
    """Returns a deep copy of `settings` for ONE region (never mutates the input).
    Without a `regions:` map the original single-server setup is returned as is."""
    region = clean_requested(region)
    already = (settings.get("source") or {}).get("region")
    if already:                                    # resolving twice must not double-suffix names
        if region is None or same_region(already, region):
            return settings
        raise RegionError(f'Settings are already resolved for "{already}", not "{region}".')

    index = _index(settings)
    if not index:
        if region:
            raise RegionError(f'--region "{region}" was given but no `regions:` are '
                              "configured in config/settings.yaml.")
        return settings
    if not region:
        raise RegionError(f'--region is required. Choose one of: '
                          f'{", ".join(chr(34) + r + chr(34) for r in available_regions(settings))}.')
    if is_all(region):
        raise RegionError('"all" must be expanded with regions_to_run() before resolving.')

    for section in ("source", "gcp", "azure"):
        if not isinstance(settings.get(section), dict):
            raise RegionError(f"config/settings.yaml is missing the `{section}:` section.")
    for key in ("project_id", "bq_dataset", "gcs_bucket"):
        if not settings["gcp"].get(key):
            raise RegionError(f"config/settings.yaml is missing gcp.{key}.")

    canonical = _canonical(settings, region)
    entry = _entry(settings, canonical)

    problem = _host_problem(entry.get("host"))
    if problem:
        raise RegionError(f'Region "{canonical}" {problem} — fix regions."{canonical}".host '
                          "in config/settings.yaml.")
    slug = region_slug(canonical)
    if not slug:
        raise RegionError(f'Region name "{canonical}" has no usable letters or digits.')

    location = str(entry.get("gcp_location") or "").strip()
    if not location:
        raise RegionError(f'Region "{canonical}" has no gcp_location — set regions."{canonical}"'
                          ".gcp_location (e.g. us-east4) so its GCS bucket and BigQuery datasets "
                          "are created in the matching GCP region.")
    if not _LOCATION_RE.match(location):
        raise RegionError(f'Region "{canonical}" has an invalid gcp_location "{location}" '
                          "(expected something like us-east4, europe-west3 or US).")

    gcp = settings["gcp"]
    bucket = str(entry.get("gcs_bucket") or f"{gcp['gcs_bucket']}-{slug.replace('_', '-')}").strip()
    control_ds = str(entry.get("bq_dataset") or f"{gcp['bq_dataset']}_{slug}").strip()
    if not _BUCKET_RE.match(bucket) or bucket.startswith("goog") or "google" in bucket:
        raise RegionError(f'Region "{canonical}" would use the GCS bucket name "{bucket}", which is '
                          "not valid (3-63 chars, lowercase letters/digits/-/_/., not containing "
                          '"google"). Set regions."' + canonical + '".gcs_bucket to a shorter name.')
    if not _DATASET_RE.match(control_ds):
        raise RegionError(f'Region "{canonical}" would use the BigQuery dataset "{control_ds}", which '
                          "is not valid (letters, digits and underscores only).")

    resolved = copy.deepcopy(settings)
    src = resolved["source"]
    src["host"] = str(entry["host"]).strip()
    src["region"] = canonical
    src["region_slug"] = slug
    if entry.get("database"):
        src["database"] = entry["database"]
    if not src.get("database"):
        raise RegionError(f'No database set for region "{canonical}" — set source.database or '
                          f'regions."{canonical}".database in config/settings.yaml.')
    if entry.get("storage_account"):
        # also use that account's own credentials: AZURE_STORAGE_CONNECTION_STRING_<REGION>
        # and AZURE_SAS_TOKEN_<REGION> (see src/config/connection.py).
        resolved["azure"]["storage_account"] = entry["storage_account"]

    resolved["gcp"]["location"] = location
    resolved["gcp"]["gcs_bucket"] = bucket
    resolved["gcp"]["bq_dataset"] = control_ds

    rg = str(entry.get("resource_group") or "").strip()
    if rg:
        src["resource_group"] = rg
    src["server"] = _server_of(entry)

    if multi_project_enabled(settings):
        if not rg:
            raise RegionError(f'multi_project is enabled but region "{canonical}" has no resource_group — '
                              f'set regions."{canonical}".resource_group in config/settings.yaml.')
        info = target_info(settings, canonical)
        resolved["gcp"]["base_project_id"] = settings["gcp"]["project_id"]   # the connectivity project
        resolved["gcp"]["project_id"] = info["project_id"]
        resolved["gcp"]["gcs_bucket"] = info["bucket"]
        src["dataset_prefix"] = info["dataset_prefix"]
        src["flat_naming"] = flat_naming(settings)
        if not _BUCKET_RE.match(info["bucket"]):
            raise RegionError(f'Region "{canonical}" would use the GCS bucket name "{info["bucket"]}", which is not valid.')
    return resolved
