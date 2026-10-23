"""
catalog_reader.py — every base table of one database, with its columns, its
primary key, its usable unique keys and a row estimate, in three catalog
queries per database (not two queries per table).

Read from sys.* views, so it also sees things INFORMATION_SCHEMA hides:
CLR types (geography, hierarchyid), alias types resolved to their base type,
identity / computed flags and unique indexes. External tables and this
pipeline's own shadow tables are skipped.

Returns {(schema, table): TableEntry-dict}:
  schema, name, columns[{name, data_type, max_length, precision, scale,
  datetime_precision, is_nullable, is_identity, is_computed, ordinal}],
  primary_key [cols], unique_keys [[cols], ...] (non-filtered, all NOT NULL),
  row_estimate
"""
from sqlalchemy import text

from src.common import log
from src.discovery.shadow_table_naming import is_shadow_table

LOG = log.get("catalog")

SYSTEM_SCHEMAS = {
    "sys", "information_schema", "guest", "db_accessadmin", "db_backupoperator", "db_datareader",
    "db_datawriter", "db_ddladmin", "db_denydatareader", "db_denydatawriter", "db_owner",
    "db_securityadmin", "cdc",
}

_COLUMNS_SQL = """
SELECT s.name AS schema_name, t.name AS table_name, c.column_id, c.name AS column_name,
       CASE WHEN ty.is_assembly_type = 1 THEN ty.name ELSE TYPE_NAME(ty.system_type_id) END AS data_type,
       c.max_length, c.precision, c.scale, c.is_nullable, c.is_identity, c.is_computed
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE t.is_ms_shipped = 0 AND t.is_external = 0 {filter}
ORDER BY s.name, t.name, c.column_id
"""

_KEYS_SQL = """
SELECT s.name AS schema_name, t.name AS table_name, i.index_id, i.is_primary_key, i.has_filter,
       ic.key_ordinal, c.name AS column_name, c.is_nullable
FROM sys.indexes i
JOIN sys.tables t ON t.object_id = i.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.index_columns ic ON ic.object_id = i.object_id AND ic.index_id = i.index_id AND ic.key_ordinal > 0
JOIN sys.columns c ON c.object_id = ic.object_id AND c.column_id = ic.column_id
WHERE i.is_unique = 1 AND i.is_disabled = 0 AND t.is_ms_shipped = 0 AND t.is_external = 0 {filter}
ORDER BY s.name, t.name, i.index_id, ic.key_ordinal
"""

_ROWS_SQL = """
SELECT s.name AS schema_name, t.name AS table_name, SUM(p.rows) AS row_estimate
FROM sys.partitions p
JOIN sys.tables t ON t.object_id = p.object_id
JOIN sys.schemas s ON s.schema_id = t.schema_id
WHERE p.index_id IN (0, 1) AND t.is_ms_shipped = 0 AND t.is_external = 0 {filter}
GROUP BY s.name, t.name
"""

_CHAR_BYTES_PER_CHAR = {"nchar": 2, "nvarchar": 2}
_FRACTIONAL_TYPES = {"datetime2", "time", "datetimeoffset"}


def _filter_sql(schemas: list[str] | None, table: tuple[str, str] | None) -> tuple[str, dict]:
    params, parts = {}, []
    if table:
        parts.append("AND s.name = :f_schema AND t.name = :f_table")
        params.update(f_schema=table[0], f_table=table[1])
    elif schemas:
        names = []
        for i, sch in enumerate(schemas):
            params[f"f_s{i}"] = sch
            names.append(f":f_s{i}")
        parts.append(f"AND s.name IN ({', '.join(names)})")
    return " ".join(parts), params


def _column(row) -> dict:
    data_type = str(row.data_type).lower()
    max_length = row.max_length
    if max_length is not None and max_length > 0 and data_type in _CHAR_BYTES_PER_CHAR:
        max_length = max_length // 2
    return {
        "name": row.column_name,
        "data_type": data_type,
        "max_length": max_length,
        "precision": row.precision,
        "scale": row.scale,
        "datetime_precision": row.scale if data_type in _FRACTIONAL_TYPES else None,
        "is_nullable": bool(row.is_nullable),
        "is_identity": bool(row.is_identity),
        "is_computed": bool(row.is_computed),
        "ordinal": row.column_id,
    }


class CatalogReader:
    def __init__(self, settings: dict, engine=None):
        self.source_cfg = settings["source"]
        if self.source_cfg.get("type", "mssql") != "mssql":
            raise ValueError(f"Unsupported source.type '{self.source_cfg.get('type')}' — only 'mssql' is supported.")
        self._engine = engine

    def _get_engine(self):
        if self._engine is None:
            from src.config.connection import build_sql_engine
            self._engine = build_sql_engine(self.source_cfg)
        return self._engine

    def _rows(self, sql: str, params: dict):
        with self._get_engine().connect() as conn:
            return conn.execute(text(sql), params).fetchall()

    def read(self, schemas: list[str] | None = None, table: tuple[str, str] | None = None) -> dict:
        """All base tables (optionally only `schemas`, or just one (schema, table))."""
        flt, params = _filter_sql(schemas, table)
        tables: dict[tuple[str, str], dict] = {}
        for r in self._rows(_COLUMNS_SQL.format(filter=flt), params):
            if str(r.schema_name).lower() in SYSTEM_SCHEMAS or is_shadow_table(r.table_name):
                continue
            key = (r.schema_name, r.table_name)
            entry = tables.setdefault(key, {"schema": r.schema_name, "name": r.table_name, "columns": [],
                                            "primary_key": [], "unique_keys": [], "row_estimate": None})
            entry["columns"].append(_column(r))

        indexes: dict[tuple, dict] = {}
        for r in self._rows(_KEYS_SQL.format(filter=flt), params):
            key = (r.schema_name, r.table_name)
            if key not in tables:
                continue
            idx = indexes.setdefault((key, r.index_id), {"pk": bool(r.is_primary_key), "filtered": bool(r.has_filter),
                                                         "nullable": False, "cols": []})
            idx["cols"].append(r.column_name)
            idx["nullable"] = idx["nullable"] or bool(r.is_nullable)
        for (key, _), idx in sorted(indexes.items(), key=lambda kv: kv[0][1]):
            if idx["pk"]:
                tables[key]["primary_key"] = idx["cols"]
            elif not idx["filtered"] and not idx["nullable"]:
                tables[key]["unique_keys"].append(idx["cols"])

        for r in self._rows(_ROWS_SQL.format(filter=flt), params):
            key = (r.schema_name, r.table_name)
            if key in tables:
                tables[key]["row_estimate"] = int(r.row_estimate or 0)

        LOG.debug("Catalog: %d table(s) read", len(tables))
        return tables

    def list_schemas(self) -> list[str]:
        return sorted({s for s, _ in self.read().keys()})
