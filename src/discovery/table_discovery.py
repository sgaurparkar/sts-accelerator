"""
table_discovery.py

The single source of truth for "what tables exist and what is their
primary key" — read straight from SQL Server's INFORMATION_SCHEMA /
sys catalog views. Nothing here is hardcoded per table: add a table to
the source database and it shows up on the next discovery run with no
code or config change.

Schemas are dynamic too (see list_schemas()): leave source.schemas
unset/empty in config/settings.yaml (or set it to "*") and every
schema that owns at least one base table is discovered straight off
SQL Server, so `--all` picks up a brand-new schema with zero config
change. Set source.schemas to an explicit list to scope the pipeline
to only those schemas instead.

SQLite support has been removed entirely — this project only talks to
real SQL Server (Azure SQL / on-prem) via SQLAlchemy + pyodbc.
"""
import os

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from src.discovery.shadow_table_naming import is_shadow_table


class TableDiscovery:

    # Built-in SQL Server schemas that are never real source data —
    # excluded from auto-discovery so `--all` (with no explicit
    # source.schemas configured) never tries to "migrate" SQL Server's
    # own internal/security schemas. Matched case-insensitively.
    _SYSTEM_SCHEMAS = {
        "sys", "information_schema", "guest",
        "db_accessadmin", "db_backupoperator", "db_datareader",
        "db_datawriter", "db_ddladmin", "db_denydatareader",
        "db_denydatawriter", "db_owner", "db_securityadmin",
    }

    def __init__(self, config: dict):
        self.source_cfg = config["source"]
        if self.source_cfg.get("type") != "mssql":
            raise ValueError(
                f"Unsupported source.type '{self.source_cfg.get('type')}' — "
                "only 'mssql' is supported."
            )
        self._engine: Engine | None = None

    def _get_engine(self) -> Engine:
        if self._engine is None:
            try:
                user = os.environ["AZURE_SQL_USERNAME"]
                pwd = os.environ["AZURE_SQL_PASSWORD"]
            except KeyError as e:
                raise RuntimeError(
                    f"Missing required environment variable {e}. Set "
                    "AZURE_SQL_USERNAME and AZURE_SQL_PASSWORD in .env "
                    "(see .env.example)."
                ) from e
            host = self.source_cfg.get("host")
            database = self.source_cfg.get("database")
            if not host or not database:
                raise RuntimeError(
                    "config/settings.yaml is missing source.host or "
                    "source.database."
                )
            driver = self.source_cfg.get("driver", "ODBC Driver 18 for SQL Server")
            conn_str = (
                f"mssql+pyodbc://{user}:{pwd}@{host}/{database}"
                f"?driver={driver.replace(' ', '+')}"
            )
            self._engine = create_engine(conn_str, pool_pre_ping=True, fast_executemany=True)
        return self._engine

    def list_schemas(self) -> list[str]:
        """Schemas to discover tables from.

        - If source.schemas in config/settings.yaml is a non-empty list,
          use exactly that list (lets you scope the pipeline to
          specific schemas).
        - If it's unset, empty (`[]`/`null`), or the literal string
          "*", every schema that owns at least one base table is
          discovered dynamically from SQL Server (system/security
          schemas excluded) — so leaving it unset makes `--all` (and
          the DAGs) genuinely span every schema in the source
          database, not just a hardcoded one.
        """
        configured = self.source_cfg.get("schemas")
        if configured and configured != "*":
            return list(configured)
        return self._discover_all_schemas()

    def _discover_all_schemas(self) -> list[str]:
        query = text(
            """
            SELECT DISTINCT TABLE_SCHEMA
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE'
            ORDER BY TABLE_SCHEMA
            """
        )
        with self._get_engine().connect() as conn:
            rows = conn.execute(query).fetchall()
        return [r[0] for r in rows if r[0].lower() not in self._SYSTEM_SCHEMAS]

    def list_tables(self, schema: str) -> list[str]:
        """Every base table in `schema`, discovered dynamically — no
        manual list. Excludes this pipeline's own internal shadow
        tables (see shadow_table_naming.py): those exist only as a
        temporary paging aid for no-PK tables and must never be
        treated as source tables to migrate in their own right — doing
        so would recursively shadow-a-shadow and corrupt the run, which
        is exactly what happened before this filter existed."""
        query = text(
            """
            SELECT TABLE_NAME
            FROM INFORMATION_SCHEMA.TABLES
            WHERE TABLE_TYPE = 'BASE TABLE' AND TABLE_SCHEMA = :schema
            ORDER BY TABLE_NAME
            """
        )
        with self._get_engine().connect() as conn:
            rows = conn.execute(query, {"schema": schema}).fetchall()
        return [r[0] for r in rows if not is_shadow_table(r[0])]

    def schema_exists(self, schema: str) -> bool:
        """True if `schema` is one of the (possibly auto-discovered)
        schemas this pipeline would consider — used to fail fast with
        a clear message when --schema names something that isn't
        there, instead of silently returning zero tables."""
        return schema in self.list_schemas()

    def list_all_tables(self) -> list[dict]:
        """[{'schema': 'dbo', 'name': 'orders'}, ...] across every configured
        (or auto-discovered) schema."""
        out = []
        for schema in self.list_schemas():
            for name in self.list_tables(schema):
                out.append({"schema": schema, "name": name})
        return out

    def get_primary_key(self, table_name: str, schema: str) -> list[str]:
        """Ordered primary-key column(s) for a table, straight from SQL Server.

        Returns [] if the table has no primary key — in that case the
        pipeline falls back to a single-batch (non-resumable) extract for
        that table and logs a warning, since keyset pagination needs a key.
        """
        query = text(
            """
            SELECT KU.COLUMN_NAME
            FROM INFORMATION_SCHEMA.TABLE_CONSTRAINTS AS TC
            JOIN INFORMATION_SCHEMA.KEY_COLUMN_USAGE AS KU
              ON TC.CONSTRAINT_NAME = KU.CONSTRAINT_NAME
             AND TC.TABLE_SCHEMA = KU.TABLE_SCHEMA
            WHERE TC.CONSTRAINT_TYPE = 'PRIMARY KEY'
              AND TC.TABLE_NAME = :table
              AND TC.TABLE_SCHEMA = :schema
            ORDER BY KU.ORDINAL_POSITION
            """
        )
        with self._get_engine().connect() as conn:
            rows = conn.execute(query, {"table": table_name, "schema": schema}).fetchall()
        return [r[0] for r in rows]
