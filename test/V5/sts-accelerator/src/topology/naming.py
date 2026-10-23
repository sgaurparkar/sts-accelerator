"""
naming.py

Every GCP name the accelerator creates is derived here, from the Azure
hierarchy, so the mapping is deterministic and lives in ONE place:

    Azure                               GCP
    ─────────────────────────────────   ─────────────────────────────────────────
    Resource Group  ResourceGroup1   -> Project  resourcegroup1
    SQL Server      SQLServer1   ┐
    Database        SalesDB      ┘   -> Dataset  sqlserver1_salesdb
    Schema.Table    dbo.Orders       -> Table    Orders
                    sales.Orders     -> Table    sales__Orders
    Azure location  centralindia     -> location asia-south1   (location_mapping.py)

    landing bucket  <project_id>-landing-<location>            resourcegroup1-landing-asia-south1
    control dataset migration_control_<location>               migration_control_asia_south1
    object prefix   <rg>/<server>/<database>                   resourcegroup1/sqlserver1/salesdb

GCP naming rules enforced here:
  * project id   6-30 chars, [a-z][a-z0-9-]*[a-z0-9], globally unique
  * dataset id   letters, digits, underscores, <= 1024 chars
  * bucket name  3-63 chars, [a-z0-9][a-z0-9._-]*[a-z0-9], no "goog"/"google", globally unique
  * table name   letters, digits, underscores, hyphens (sanitised from SQL Server names)

Everything is overridable in config/settings.yaml (see the `gcp:` section).
"""
import hashlib
import re


class NamingError(ValueError):
    """A derived GCP name is invalid and cannot be fixed automatically."""


_PROJECT_RE = re.compile(r"^[a-z][a-z0-9-]{4,28}[a-z0-9]$")
_DATASET_RE = re.compile(r"^[A-Za-z0-9_]{1,1024}$")
_BUCKET_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,61}[a-z0-9]$")


def _hash4(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:4]


def slug(text, sep: str = "_") -> str:
    """Lower-case, every run of non [a-z0-9] chars collapsed to `sep`."""
    return re.sub(r"[^a-z0-9]+", sep, str(text or "").lower()).strip(sep)


def short_server_name(server: str) -> str:
    """'sqlserver1.database.windows.net' -> 'sqlserver1' (Azure server names are DNS labels)."""
    return str(server or "").strip().split(".")[0]


def _key(text) -> str:
    """Case/punctuation-insensitive lookup key for override maps."""
    return re.sub(r"[^a-z0-9]", "", str(text or "").lower())


def _lookup(overrides: dict | None, *candidates) -> str | None:
    norm = {_key(k): v for k, v in (overrides or {}).items() if v}
    for c in candidates:
        if _key(c) in norm:
            return str(norm[_key(c)]).strip()
    return None


# ---------------------------------------------------------------- projects --
def project_id_for_resource_group(resource_group: str, gcp_cfg: dict | None = None) -> str:
    """Resource Group -> GCP project id.

    'ResourceGroup1' -> 'resourcegroup1' (plus optional gcp.projects.id_prefix/
    id_suffix, because project ids are GLOBALLY unique across all of GCP and a
    plain name like 'resourcegroup1' may already be taken by someone else).
    gcp.projects.id_overrides pins a specific id for a resource group.
    """
    pcfg = (gcp_cfg or {}).get("projects") or {}
    pinned = _lookup(pcfg.get("id_overrides"), resource_group)
    if pinned:
        if not _PROJECT_RE.match(pinned):
            raise NamingError(f'gcp.projects.id_overrides["{resource_group}"] = "{pinned}" is not a valid '
                              "GCP project id (6-30 chars, lowercase letters/digits/hyphens, starts with a letter).")
        return pinned

    prefix = slug(pcfg.get("id_prefix") or "", "-")
    suffix = slug(pcfg.get("id_suffix") or "", "-")
    core = slug(resource_group, "-")
    if not core:
        raise NamingError(f'Resource group "{resource_group}" has no letters or digits to build a project id from.')
    parts = [p for p in (prefix, core, suffix) if p]
    pid = "-".join(parts)
    if not pid[0].isalpha():
        pid = f"rg-{pid}"
    while len(pid) < 6:
        pid = f"{pid}-gcp"
    if len(pid) > 30:
        # keep prefix/suffix intact, shorten the RG part, add a stable hash so two
        # long RG names that share a beginning never map to the same project
        tail = "-" + "-".join([p for p in (suffix,) if p] + [_hash4(resource_group)])
        head = f"{prefix}-" if prefix else ""
        room = 30 - len(head) - len(tail)
        if room < 1:
            raise NamingError("gcp.projects.id_prefix/id_suffix are too long to fit a 30-char project id.")
        pid = f"{head}{core[:room].rstrip('-')}{tail}"
        if not pid[0].isalpha():
            pid = "r" + pid[1:]
    if not _PROJECT_RE.match(pid):
        raise NamingError(f'Could not derive a valid project id from resource group "{resource_group}" '
                          f'(got "{pid}"). Set gcp.projects.id_overrides["{resource_group}"].')
    return pid


def project_display_name(resource_group: str) -> str:
    """Display name: 4-30 chars of letters, digits, spaces, hyphens, quotes, ! ."""
    name = re.sub(r"[^A-Za-z0-9 '\"!-]+", "-", str(resource_group)).strip(" -")[:30].strip(" -")
    while len(name) < 4:
        name = f"{name} rg".strip()
    return name


# ---------------------------------------------------------------- datasets --
def dataset_id_for(server: str, database: str, gcp_cfg: dict | None = None,
                   resource_group: str = "") -> str:
    """SQL Server + Database -> dataset id: 'SQLServer1' + 'SalesDB' -> 'sqlserver1_salesdb'.

    gcp.datasets.name_template (default "{server}_{database}") may also use
    {resource_group}. gcp.datasets.overrides pins one, keyed "server/database".
    """
    dcfg = (gcp_cfg or {}).get("datasets") or {}
    srv = short_server_name(server)
    pinned = _lookup(dcfg.get("overrides"), f"{srv}/{database}", f"{srv}.{database}")
    if pinned:
        ds = pinned
    else:
        template = dcfg.get("name_template") or "{server}_{database}"
        try:
            raw = template.format(server=slug(srv), database=slug(database),
                                  resource_group=slug(resource_group))
        except KeyError as e:
            raise NamingError(f"gcp.datasets.name_template uses unknown placeholder {e}; "
                              "allowed: {server}, {database}, {resource_group}.") from None
        ds = re.sub(r"_+", "_", re.sub(r"[^A-Za-z0-9_]", "_", raw)).strip("_")
        if ds and ds[0].isdigit():
            ds = f"ds_{ds}"
    if not _DATASET_RE.match(ds or ""):
        raise NamingError(f'Dataset id "{ds}" derived for {server}/{database} is not valid '
                          "(letters, digits, underscores only).")
    return ds


def control_dataset_id(gcp_location: str, gcp_cfg: dict | None = None) -> str:
    """One control dataset per (project, location): checkpoints / audit / logs /
    report stay in the same location (and data-residency zone) as the data."""
    prefix = ((gcp_cfg or {}).get("control") or {}).get("dataset_prefix") or "migration_control"
    ds = f"{slug(prefix)}_{slug(gcp_location)}"
    if not _DATASET_RE.match(ds):
        raise NamingError(f'Control dataset id "{ds}" is not valid.')
    return ds


# ----------------------------------------------------------------- buckets --
def landing_bucket_name(project_id: str, gcp_location: str, gcp_cfg: dict | None = None) -> str:
    """One landing bucket per (project, location). Bucket names are global, so
    the (globally unique) project id is part of the default template."""
    bcfg = (gcp_cfg or {}).get("landing_bucket") or {}
    template = bcfg.get("name_template") or "{project_id}-landing-{location}"
    try:
        raw = template.format(project_id=project_id, location=slug(gcp_location, "-"))
    except KeyError as e:
        raise NamingError(f"gcp.landing_bucket.name_template uses unknown placeholder {e}; "
                          "allowed: {project_id}, {location}.") from None
    name = re.sub(r"[^a-z0-9._-]+", "-", raw.lower()).strip("-._")
    if len(name) > 63:
        name = f"{name[:58].rstrip('-._')}-{_hash4(raw)}"
    if not _BUCKET_RE.match(name) or name.startswith("goog") or "google" in name:
        raise NamingError(f'Landing bucket name "{name}" is not valid — change gcp.landing_bucket.name_template.')
    return name


# ------------------------------------------------------------------ tables --
def bq_table_name(schema: str, table: str, gcp_cfg: dict | None = None) -> str:
    """Schema.Table -> BigQuery table name inside the server_database dataset.

    One dataset now holds every schema of a database, so tables from
    non-default schemas get a schema prefix to stay unique:
        gcp.table_naming.schema_prefix = non_default (default): dbo.Orders -> Orders,
                                                                sales.Orders -> sales__Orders
                                       = always:               dbo__Orders, sales__Orders
                                       = never:                Orders (planner rejects clashes)
    """
    tcfg = (gcp_cfg or {}).get("table_naming") or {}
    mode = str(tcfg.get("schema_prefix") or "non_default").lower()
    default_schema = str(tcfg.get("default_schema") or "dbo")
    sep = tcfg.get("separator") if tcfg.get("separator") is not None else "__"
    if mode not in ("non_default", "always", "never"):
        raise NamingError(f'gcp.table_naming.schema_prefix "{mode}" is not valid — use non_default, always or never.')

    def clean(x):
        return re.sub(r"[^A-Za-z0-9_-]", "_", str(x))

    prefix = (mode == "always") or (mode == "non_default" and str(schema).lower() != default_schema.lower())
    name = f"{clean(schema)}{sep}{clean(table)}" if prefix else clean(table)
    if not name or len(name) > 1024:
        raise NamingError(f"Cannot derive a BigQuery table name for {schema}.{table}.")
    return name


# ------------------------------------------------------------------- paths --
def object_prefix(resource_group: str, server: str, database: str) -> str:
    """Path segment used in Azure Blob staging, GCS landing and local Parquet,
    so two databases never share a folder: 'resourcegroup1/sqlserver1/salesdb'."""
    return "/".join(slug(x) for x in (resource_group, short_server_name(server), database))


def secret_suffixes(server: str, azure_location: str | None = None,
                    resource_group: str | None = None) -> list[str]:
    """Env-var suffixes tried (most specific first) when looking up a secret for
    one server:  AZURE_SQL_PASSWORD_SQLSERVER1 -> _RESOURCEGROUP1 -> _CENTRALINDIA -> AZURE_SQL_PASSWORD."""
    out = []
    for part in (short_server_name(server), resource_group, azure_location):
        s = slug(part).upper()
        if s and s not in out:
            out.append(s)
    return out


# ----------------------------------------------------------------- columns --
_RESERVED_COLUMN_PREFIXES = ("_table_", "_file_", "_partition", "_row_timestamp", "__root__", "_colidentifier")


def bq_column_names(names: list[str], mode: str = "sanitize") -> list[str]:
    """SQL Server column names -> BigQuery column names, same order, unique
    ignoring case (BigQuery column names are case-insensitive).

    sanitize (default): letters/digits/underscore only, never starting with a
        digit or a reserved prefix, at most 300 chars ("Order Date" -> Order_Date)
    keep: the original name (BigQuery flexible column names) — characters flexible
        names don't allow (backtick, dot, brackets, ...) become "_" — made unique
    """
    mode = str(mode or "sanitize").lower()
    if mode not in ("sanitize", "keep"):
        raise NamingError(f'gcp.column_naming "{mode}" is not valid — use sanitize or keep.')
    out, seen = [], set()
    for raw in names:
        name = str(raw)
        if mode == "sanitize":
            name = re.sub(r"[^A-Za-z0-9_]", "_", name) or "col"
            if name[0].isdigit():
                name = f"_{name}"
            if name.lower().startswith(_RESERVED_COLUMN_PREFIXES):
                name = f"c{name}"
            name = name[:300]
        else:
            name = re.sub(r"[^\w &%=+:'<>#|-]", "_", name).strip() or "col"
            name = name[:300]
        candidate, n = name, 2
        while candidate.lower() in seen:
            suffix = f"_{n}"
            candidate = f"{name[:300 - len(suffix)]}{suffix}"
            n += 1
        seen.add(candidate.lower())
        out.append(candidate)
    return out


def path_component(text: str) -> str:
    """A schema/table name made safe for object paths and load wildcards; a
    stable hash is added whenever characters had to be replaced, so
    'Order Details' and 'Order_Details' never share a folder."""
    raw = str(text)
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", raw).strip(".") or "x"
    return safe if safe == raw else f"{safe}-{_hash4(raw)}"
