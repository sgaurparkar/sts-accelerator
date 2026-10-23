"""
object_catalog.py — views, stored procedures (and optionally functions / triggers)
of one database, with their ORIGINAL T-SQL text, read from sys.* catalog views.

Two steps, so listing / --dry-run never pulls definitions:

  read_index(kinds, schemas)  one query: every object's identity, type, dates, flags
  read_details(objects)       definitions (sys.sql_modules), parameters (sys.parameters),
                              view columns (sys.columns) and dependencies
                              (sys.sql_expression_dependencies), in batches of object ids

Nothing is translated or rewritten: `definition` is exactly what SQL Server stores
(the CREATE VIEW / CREATE PROCEDURE text as it was written, comments included).

A definition can be missing:
  ENCRYPTED       created WITH ENCRYPTION — SQL Server does not return the text
  NO_PERMISSION   the login lacks VIEW DEFINITION (GRANT VIEW DEFINITION TO <login>)
"""
from sqlalchemy import text

from src.catalog.catalog_reader import SYSTEM_SCHEMAS
from src.common import log
from src.objects.object_types import kind_of, type_codes

LOG = log.get("object_catalog")

_BATCH = 400

_INDEX_SQL = """
SELECT o.object_id, s.name AS schema_name, o.name AS object_name, RTRIM(o.type) AS type_code,
       o.type_desc, o.create_date, o.modify_date,
       CAST(ISNULL(OBJECTPROPERTY(o.object_id, 'IsEncrypted'), 0) AS int) AS is_encrypted,
       ps.name AS parent_schema, po.name AS parent_name
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.objects po ON po.object_id = o.parent_object_id AND o.parent_object_id <> 0
LEFT JOIN sys.schemas ps ON ps.schema_id = po.schema_id
WHERE o.is_ms_shipped = 0 AND RTRIM(o.type) IN ({types}) {filter}
ORDER BY s.name, o.name
"""

_MODULES_SQL = """
SELECT m.object_id, m.definition, m.uses_ansi_nulls, m.uses_quoted_identifier, m.is_schema_bound,
       CAST(ISNULL(m.execute_as_principal_id, -1) AS int) AS execute_as_principal_id
FROM sys.sql_modules m
WHERE m.object_id IN ({ids})
"""

_PARAMS_SQL = """
SELECT p.object_id, p.parameter_id, p.name AS parameter_name, TYPE_NAME(p.user_type_id) AS data_type,
       p.max_length, p.precision, p.scale, p.is_output, p.has_default_value, p.is_readonly
FROM sys.parameters p
WHERE p.object_id IN ({ids})
ORDER BY p.object_id, p.parameter_id
"""

_COLUMNS_SQL = """
SELECT c.object_id, c.column_id, c.name AS column_name, TYPE_NAME(c.user_type_id) AS data_type,
       c.max_length, c.precision, c.scale, c.is_nullable
FROM sys.columns c
WHERE c.object_id IN ({ids})
ORDER BY c.object_id, c.column_id
"""

_DEPS_SQL = """
SELECT DISTINCT d.referencing_id AS object_id,
       COALESCE(d.referenced_schema_name, rs.name) AS ref_schema,
       d.referenced_entity_name AS ref_name,
       d.referenced_database_name AS ref_database,
       d.referenced_server_name AS ref_server,
       RTRIM(ro.type) AS ref_type
FROM sys.sql_expression_dependencies d
LEFT JOIN sys.objects ro ON ro.object_id = d.referenced_id
LEFT JOIN sys.schemas rs ON rs.schema_id = ro.schema_id
WHERE d.referencing_id IN ({ids}) AND d.referenced_entity_name IS NOT NULL
"""

_N_CHAR = {"nchar", "nvarchar"}


def _iso(value):
    return value.isoformat() if hasattr(value, "isoformat") else (str(value) if value is not None else None)


def _length(data_type, max_length):
    if max_length is not None and max_length > 0 and str(data_type).lower() in _N_CHAR:
        return max_length // 2
    return max_length


def _schema_filter(schemas: list[str] | None) -> tuple[str, dict]:
    if not schemas:
        return "", {}
    params, names = {}, []
    for i, sch in enumerate(schemas):
        params[f"f_s{i}"] = sch
        names.append(f":f_s{i}")
    return f"AND s.name IN ({', '.join(names)})", params


class ObjectCatalogReader:
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

    def _rows(self, sql: str, params: dict | None = None):
        with self._get_engine().connect() as conn:
            return conn.execute(text(sql), params or {}).fetchall()

    # ------------------------------------------------------------------ index --
    def read_index(self, kinds: list[str], schemas: list[str] | None = None) -> list[dict]:
        codes = type_codes(kinds)
        if not codes:
            return []
        params = {f"t{i}": c for i, c in enumerate(codes)}
        flt, sparams = _schema_filter(schemas)
        params.update(sparams)
        sql = _INDEX_SQL.format(types=", ".join(f":t{i}" for i in range(len(codes))), filter=flt)
        out = []
        for r in self._rows(sql, params):
            if str(r.schema_name).lower() in SYSTEM_SCHEMAS:
                continue
            kind = kind_of(r.type_code)
            if kind is None:
                continue
            out.append({
                "object_id": int(r.object_id), "schema": r.schema_name, "name": r.object_name,
                "kind": kind, "type_code": str(r.type_code).strip(), "type_desc": r.type_desc,
                "created_at": _iso(r.create_date), "modified_at": _iso(r.modify_date),
                "is_encrypted": bool(r.is_encrypted),
                "parent_object": f"{r.parent_schema}.{r.parent_name}" if r.parent_name else None,
            })
        LOG.debug("Object index: %d object(s)", len(out))
        return out

    # ---------------------------------------------------------------- details --
    def _batched(self, sql: str, ids: list[int]):
        for i in range(0, len(ids), _BATCH):
            chunk = ", ".join(str(int(x)) for x in ids[i:i + _BATCH])
            yield from self._rows(sql.format(ids=chunk))

    def read_details(self, objects: list[dict]) -> list[dict]:
        """Returns copies of `objects` with definition, parameters, columns and
        references filled in."""
        if not objects:
            return []
        ids = [o["object_id"] for o in objects]
        modules = {int(r.object_id): r for r in self._batched(_MODULES_SQL, ids)}
        params: dict[int, list] = {}
        for r in self._batched(_PARAMS_SQL, ids):
            if not r.parameter_id:          # parameter_id 0 = a function's return value
                key = "returns"
            else:
                key = "params"
            params.setdefault(int(r.object_id), []).append((key, {
                "name": r.parameter_name, "data_type": r.data_type,
                "max_length": _length(r.data_type, r.max_length), "precision": r.precision, "scale": r.scale,
                "is_output": bool(r.is_output), "has_default": bool(r.has_default_value),
                "is_readonly": bool(r.is_readonly), "ordinal": int(r.parameter_id)}))
        view_ids = [o["object_id"] for o in objects if o["kind"] in ("view", "function")]
        columns: dict[int, list] = {}
        for r in self._batched(_COLUMNS_SQL, view_ids):
            columns.setdefault(int(r.object_id), []).append({
                "name": r.column_name, "data_type": r.data_type, "max_length": _length(r.data_type, r.max_length),
                "precision": r.precision, "scale": r.scale, "is_nullable": bool(r.is_nullable),
                "ordinal": int(r.column_id)})
        deps: dict[int, list] = {}
        for r in self._batched(_DEPS_SQL, ids):
            deps.setdefault(int(r.object_id), []).append({
                "schema": r.ref_schema, "name": r.ref_name, "database": r.ref_database, "server": r.ref_server,
                "kind": kind_of(r.ref_type) or ("table" if str(r.ref_type or "").strip() == "U" else None),
                "type_code": str(r.ref_type).strip() if r.ref_type else None})

        out = []
        for o in objects:
            m = modules.get(o["object_id"])
            definition = m.definition if m is not None else None
            if definition is not None:
                status = "OK"
            elif o["is_encrypted"]:
                status = "ENCRYPTED"
            else:
                status = "NO_PERMISSION"
            p = params.get(o["object_id"], [])
            out.append({
                **o,
                "definition": definition,
                "definition_status": status,
                "uses_ansi_nulls": bool(m.uses_ansi_nulls) if m is not None and m.uses_ansi_nulls is not None else None,
                "uses_quoted_identifier": (bool(m.uses_quoted_identifier)
                                           if m is not None and m.uses_quoted_identifier is not None else None),
                "is_schema_bound": bool(m.is_schema_bound) if m is not None and m.is_schema_bound is not None else None,
                "parameters": [v for k, v in p if k == "params"],
                "returns": next((v for k, v in p if k == "returns"), None),
                "columns": columns.get(o["object_id"], []),
                "references": sorted(deps.get(o["object_id"], []),
                                     key=lambda d: (str(d.get("database") or ""), str(d.get("schema") or ""),
                                                    str(d.get("name") or ""))),
            })
        return out
