"""
connection.py — builds the SQL Server connection for one database and looks up
secrets, so the catalog reader, the object exporter and the extractor can never
drift apart.

Secret names are built from templates (settings.yaml `secrets:`), tried in order,
most specific first. Every placeholder is the Azure name upper-cased with each run
of non-letters/digits turned into "_" (ingredion-dev-rg-central-india ->
INGREDION_DEV_RG_CENTRAL_INDIA):

  SQL logins (FIELD = USERNAME | PASSWORD | CLIENT_ID | CLIENT_SECRET)
    {RESOURCE_GROUP}_AZURE_SQL_{SERVER}_{FIELD}
        INGREDION_DEV_RG_CENTRAL_INDIA_AZURE_SQL_INGREDION_DEV_SERVER_CENTRAL_INDIAA_PASSWORD
    AZURE_SQL_{SERVER}_{FIELD}
    AZURE_SQL_{FIELD}_{SERVER}          AZURE_SQL_PASSWORD_SQLSERVER1        (previous versions)
    AZURE_SQL_{FIELD}_{RESOURCE_GROUP}
    AZURE_SQL_{FIELD}_{LOCATION}
    AZURE_SQL_{FIELD}                   one login for every server

  Azure Storage (NAME = AZURE_STORAGE_CONNECTION_STRING | AZURE_SAS_TOKEN)
    {RESOURCE_GROUP}_{NAME}  ->  {NAME}_{ACCOUNT}  ->  {NAME}_{LOCATION}  ->  {NAME}

Values come from the environment / .env first, then — with
secrets.provider: gcp_secret_manager — from Google Secret Manager (secret id =
the variable name lower-cased with hyphens).

A variable whose name differs from an expected one only in underscores (e.g.
..._EAST_USAZURE_SQL_... instead of ..._EAST_US_AZURE_SQL_...) is still used
(secrets.fuzzy_match, default true) and a warning names the typo.

source.auth:
  sql                         SQL login (USERNAME / PASSWORD)
  entra_service_principal     Microsoft Entra app (CLIENT_ID / CLIENT_SECRET, falling back to
                              AZURE_CLIENT_ID / AZURE_CLIENT_SECRET) — no SQL passwords
  entra_managed_identity      managed identity of the VM / container (optional CLIENT_ID
                              for a user-assigned identity)
"""
import os
import re
import threading

from dotenv import load_dotenv

from src.common import log

load_dotenv()

LOG = log.get("secrets")

DEFAULT_SQL_NAME_TEMPLATES = [
    "{RESOURCE_GROUP}_AZURE_SQL_{SERVER}_{FIELD}",
    "AZURE_SQL_{SERVER}_{FIELD}",
    "AZURE_SQL_{FIELD}_{SERVER}",
    "AZURE_SQL_{FIELD}_{RESOURCE_GROUP}",
    "AZURE_SQL_{FIELD}_{LOCATION}",
    "AZURE_SQL_{FIELD}",
]
DEFAULT_STORAGE_NAME_TEMPLATES = [
    "{RESOURCE_GROUP}_{NAME}",
    "{NAME}_{ACCOUNT}",
    "{NAME}_{LOCATION}",
    "{NAME}",
]
_PLACEHOLDER = re.compile(r"\{([A-Z_]+)\}")

_SETTINGS_SECRETS: dict = {}      # set by configure_secrets() from settings.yaml `secrets:`
_SM_CACHE: dict[str, str | None] = {}
_WARNED: set[str] = set()
_WARN_GUARD = threading.Lock()


def configure_secrets(secrets_cfg: dict | None) -> None:
    _SETTINGS_SECRETS.clear()
    _SETTINGS_SECRETS.update(secrets_cfg or {})


def env_part(text) -> str:
    """'ingredion-dev-rg-central-india' -> 'INGREDION_DEV_RG_CENTRAL_INDIA'."""
    return re.sub(r"[^A-Z0-9]+", "_", str(text or "").upper()).strip("_")


def _compact(name: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", str(name).upper())


def _as_suffixes(suffixes) -> list[str]:
    if not suffixes:
        return []
    if isinstance(suffixes, str):
        suffixes = [suffixes]
    return [str(s).upper() for s in suffixes if s]


def expand_templates(templates: list[str], **parts) -> list[str]:
    """Candidate variable names from templates; a template whose placeholder has no
    value (e.g. {RESOURCE_GROUP} in inventory mode without one) is left out."""
    values = {k.upper(): env_part(v) for k, v in parts.items() if v not in (None, "")}
    out = []
    for template in templates:
        names = _PLACEHOLDER.findall(template)
        if any(not values.get(n) for n in names):
            continue
        name = _PLACEHOLDER.sub(lambda m: values[m.group(1)], template)
        name = re.sub(r"_+", "_", name).strip("_")
        if name and name not in out:
            out.append(name)
    return out


def _secret_manager(var: str) -> str | None:
    if str(_SETTINGS_SECRETS.get("provider") or "env").lower() != "gcp_secret_manager":
        return None
    project = _SETTINGS_SECRETS.get("project") or os.environ.get("GOOGLE_CLOUD_PROJECT")
    if not project:
        return None
    secret_id = var.lower().replace("_", "-")
    key = f"{project}/{secret_id}"
    if key in _SM_CACHE:
        return _SM_CACHE[key]
    value = None
    try:
        from google.api_core import exceptions as gexc
        from google.cloud import secretmanager
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{project}/secrets/{secret_id}/versions/latest"
        try:
            value = client.access_secret_version(name=name).payload.data.decode("utf-8")
        except (gexc.NotFound, gexc.PermissionDenied):
            value = None
    except ImportError as e:
        raise RuntimeError("secrets.provider gcp_secret_manager needs google-cloud-secret-manager") from e
    _SM_CACHE[key] = value
    return value


def _warn_once(key: str, msg: str, *args) -> None:
    with _WARN_GUARD:
        if key in _WARNED:
            return
        _WARNED.add(key)
    LOG.warning(msg, *args)


def resolve_secret(candidates: list[str]) -> tuple[str | None, str | None]:
    """(value, variable it came from) for the first candidate that is set:
    exact environment names, then names that differ only in underscores / case
    (secrets.fuzzy_match), then Secret Manager. (None, None) if none is set."""
    for var in candidates:
        value = os.environ.get(var)
        if value and value.strip():
            return value.strip(), var
    if _SETTINGS_SECRETS.get("fuzzy_match", True):
        by_compact: dict[str, str] = {}
        for k, v in os.environ.items():
            if v and v.strip():
                by_compact.setdefault(_compact(k), k)
        for var in candidates:
            actual = by_compact.get(_compact(var))
            if actual:
                _warn_once(f"{actual}->{var}", "Using environment variable %s for %s — the names differ only in "
                           "underscores/case; fix the name in .env to silence this warning.", actual, var)
                return os.environ[actual].strip(), actual
    for var in candidates:
        value = _secret_manager(var)
        if value and value.strip():
            return value.strip(), f"secretmanager:{var.lower().replace('_', '-')}"
    return None, None


def regional_env(name: str, suffixes=None) -> str | None:
    """First non-blank of NAME_<suffix> for each suffix (in order), then NAME —
    environment first, then the configured secret provider."""
    names = [f"{name}_{s}" for s in _as_suffixes(suffixes)] + [name]
    return resolve_secret(names)[0]


def require_env(name: str, suffixes=None, hint: str = ".env") -> str:
    value = regional_env(name, suffixes)
    if value:
        return value
    tried = [f"{name}_{s}" for s in _as_suffixes(suffixes)] + [name]
    raise RuntimeError(f"Missing secret — set one of {', '.join(tried)} in {hint} "
                       "(or Secret Manager, see secrets: in settings.yaml).")


# ------------------------------------------------------------------ SQL logins --
def sql_secret_candidates(field: str, source_cfg: dict) -> list[str]:
    templates = _SETTINGS_SECRETS.get("sql_name_templates") or DEFAULT_SQL_NAME_TEMPLATES
    from src.topology.naming import short_server_name
    return expand_templates(templates, FIELD=field,
                            RESOURCE_GROUP=source_cfg.get("resource_group"),
                            SERVER=short_server_name(source_cfg.get("server") or source_cfg.get("host") or ""),
                            DATABASE=source_cfg.get("database"),
                            LOCATION=source_cfg.get("azure_location"))


def sql_secret(field: str, source_cfg: dict, required: bool = True) -> tuple[str | None, str | None]:
    """(value, variable name) of one SQL credential for the database in source_cfg."""
    candidates = sql_secret_candidates(field, source_cfg)
    value, var = resolve_secret(candidates)
    if value is None and required:
        where = f"{source_cfg.get('resource_group') or '?'}/{source_cfg.get('server') or '?'}"
        raise RuntimeError(f"Missing {field.lower()} for {where} — set one of {', '.join(candidates)} in .env "
                           "(or Secret Manager, see secrets: in settings.yaml).")
    return value, var


# --------------------------------------------------------------- Azure Storage --
def storage_secret_candidates(name: str, config: dict) -> list[str]:
    templates = _SETTINGS_SECRETS.get("storage_name_templates") or DEFAULT_STORAGE_NAME_TEMPLATES
    src = config.get("source") or {}
    return expand_templates(templates, NAME=name, RESOURCE_GROUP=src.get("resource_group"),
                            ACCOUNT=(config.get("azure") or {}).get("storage_account"),
                            LOCATION=src.get("azure_location"), SERVER=src.get("server"))


def storage_secret(name: str, config: dict, required: bool = True) -> tuple[str | None, str | None]:
    candidates = storage_secret_candidates(name, config)
    value, var = resolve_secret(candidates)
    if value is None and required:
        raise RuntimeError(f"{name} not set — set one of {', '.join(candidates)} in .env "
                           "(or Secret Manager, see secrets: in settings.yaml).")
    return value, var


def build_sql_engine(source_cfg: dict, **engine_kwargs):
    """SQLAlchemy engine for one database (source_cfg is the per-target `source`
    section built by src/topology/targets.settings_for_target)."""
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    host = str(source_cfg.get("host") or "").strip()
    database = source_cfg.get("database")
    if not host or not database:
        raise RuntimeError("No source host/database resolved — run through a migration target "
                           "(src/topology/targets.settings_for_target).")
    port = None
    if ":" in host:
        host, _, port_text = host.rpartition(":")
        try:
            port = int(port_text)
        except ValueError:
            raise RuntimeError(f'Invalid port in source host "{host}:{port_text}".') from None

    query = {"driver": source_cfg.get("driver", "ODBC Driver 18 for SQL Server"),
             "Encrypt": "yes", "TrustServerCertificate": "no",
             "ApplicationIntent": source_cfg.get("application_intent") or "ReadWrite"}
    auth = str(source_cfg.get("auth") or "sql").lower()
    user = pwd = None
    if auth == "sql":
        user, _ = sql_secret("USERNAME", source_cfg)
        pwd, _ = sql_secret("PASSWORD", source_cfg)
    elif auth == "entra_service_principal":
        user = sql_secret("CLIENT_ID", source_cfg, required=False)[0] or require_env("AZURE_CLIENT_ID")
        pwd = sql_secret("CLIENT_SECRET", source_cfg, required=False)[0] or require_env("AZURE_CLIENT_SECRET")
        query["Authentication"] = "ActiveDirectoryServicePrincipal"
    elif auth == "entra_managed_identity":
        user = sql_secret("CLIENT_ID", source_cfg, required=False)[0]
        query["Authentication"] = "ActiveDirectoryMsi"
    else:
        raise RuntimeError(f'source.auth "{auth}" — use sql, entra_service_principal or entra_managed_identity.')

    for k, v in (source_cfg.get("odbc_options") or {}).items():
        query[str(k)] = str(v)
    url = URL.create("mssql+pyodbc", username=user, password=pwd, host=host, port=port,
                     database=database, query=query)
    connect_args = {"timeout": int(source_cfg.get("login_timeout_seconds", 60))}
    return create_engine(url, pool_pre_ping=True, pool_recycle=1800, connect_args=connect_args, **engine_kwargs)
