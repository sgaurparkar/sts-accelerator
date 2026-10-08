"""
connection.py

One place that builds the SQL Server connection and looks up secrets, so
table_discovery / schema_reader / sql_extractor can never drift apart.

Per-region secrets: each regional SQL server (or Azure storage account) may
have its own login. For a secret NAME and region slug `east_us` the lookup is

    <NAME>_EAST_US   first (region-specific)
    <NAME>           fallback (shared by every region)

e.g. AZURE_SQL_USERNAME_EAST_US, AZURE_SQL_PASSWORD_EAST_US,
AZURE_STORAGE_CONNECTION_STRING_EAST_US, AZURE_SAS_TOKEN_EAST_US.
"""
import os

from dotenv import load_dotenv

load_dotenv()


def regional_env(name: str, region_slug: str | None = None) -> str | None:
    """Region-specific env var if set, else the shared one, else None. Blank
    values count as unset."""
    candidates = []
    if region_slug:
        candidates.append(f"{name}_{region_slug.upper()}")
    candidates.append(name)
    for var in candidates:
        value = os.environ.get(var)
        if value and value.strip():
            return value
    return None


def require_env(name: str, region_slug: str | None = None, hint: str = ".env") -> str:
    value = regional_env(name, region_slug)
    if value:
        return value
    where = f"{name} (or {name}_{region_slug.upper()} for this region)" if region_slug else name
    raise RuntimeError(f"Missing required environment variable {where}. Set it in {hint} (see .env.example).")


def build_sql_engine(source_cfg: dict, **engine_kwargs):
    """SQLAlchemy engine for source_cfg (host/database/driver, optional region_slug).

    Uses URL.create so passwords/usernames containing @ : / # % etc. are quoted
    correctly instead of breaking a hand-built connection string. host may be
    "server" or "server:port".
    """
    from sqlalchemy import create_engine
    from sqlalchemy.engine import URL

    slug = source_cfg.get("region_slug")
    user = require_env("AZURE_SQL_USERNAME", slug)
    pwd = require_env("AZURE_SQL_PASSWORD", slug)
    host = str(source_cfg.get("host") or "").strip()
    database = source_cfg.get("database")
    if not host or not database:
        raise RuntimeError("config/settings.yaml is missing source.host or source.database.")
    port = None
    if ":" in host:
        host, _, port_text = host.rpartition(":")
        try:
            port = int(port_text)
        except ValueError:
            raise RuntimeError(f'Invalid port in source.host "{host}:{port_text}".') from None
    url = URL.create(
        "mssql+pyodbc", username=user, password=pwd, host=host, port=port, database=database,
        query={"driver": source_cfg.get("driver", "ODBC Driver 18 for SQL Server")},
    )
    return create_engine(url, pool_pre_ping=True, **engine_kwargs)
