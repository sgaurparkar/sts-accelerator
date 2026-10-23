"""
sql_extractor.py — reads one table from SQL Server.

Three read strategies (chosen by the planner):

  keyset   SELECT TOP (n) ... WHERE <filters> AND <key after last page> ORDER BY <key>
           Seek pagination on the primary / unique key. Every page is its own short
           query, and the last key of every committed chunk is checkpointed, so a
           failed run resumes exactly where it stopped.
  stream   one forward-only SELECT read with fetchmany(): tables with no key, no
           writes to the source. Not resumable (a failed run restarts the table).
  shadow   (opt-in) materialises ROW_NUMBER() once into a shadow table in the
           source database and then pages it like keyset.

Every parameter is CAST to the column's own T-SQL type (decl), so
  * comparisons stay index seeks (no nvarchar-vs-varchar implicit conversion)
  * a datetime key/watermark round-trips exactly (no 9-digit fraction problem)

The SELECT list is explicit (type_mapper.select_expr), never SELECT *.
"""
import datetime

from sqlalchemy import text

from src.common import log
from src.discovery.shadow_table_naming import shadow_table_name
from src.planner.type_mapper import base_type, quote

LOG = log.get("extractor")
_ROWVERSION = ("timestamp", "rowversion")


# ----------------------------------------------------------- SQL building --
def table_ref(schema: str, table: str) -> str:
    return f"{quote(schema)}.{quote(table)}"


def select_list(columns: list[dict]) -> str:
    return ", ".join(f"{c['select_expr']} AS {quote(c['name'])}" for c in columns)


def bind_value(value):
    """Python value -> what pyodbc binds. Aware datetimes become naive UTC."""
    if isinstance(value, datetime.datetime) and value.tzinfo is not None:
        return value.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    return value


def cast_param(col: dict, pname: str) -> str:
    if base_type(col["data_type"]) in _ROWVERSION:
        return f"CONVERT(binary(8), :{pname}, 1)"
    return f"CAST(:{pname} AS {col['decl']})"


def coerce_text(col: dict, text_value):
    """'2026-01-01' / '2026-01-01T10:00:00' for a column -> date or datetime."""
    if text_value is None or not isinstance(text_value, str):
        return text_value
    t = base_type(col["data_type"])
    if t == "date":
        return datetime.date.fromisoformat(text_value[:10])
    return datetime.datetime.fromisoformat(text_value)


def keyset_condition(key_cols: list[dict], last: dict | None) -> tuple[str | None, dict]:
    """Rows strictly after `last` in key order. For composite keys the leading
    column is also given as a plain >= so SQL Server can seek on it:
        k1 >= v1 AND ((k1 > v1) OR (k1 = v1 AND k2 > v2) OR ...)"""
    if not last:
        return None, {}
    params, ors = {}, []
    for i, col in enumerate(key_cols):
        params[f"k_{i}"] = bind_value(last[col["name"]])
    for i, col in enumerate(key_cols):
        parts = [f"{quote(key_cols[j]['name'])} = {cast_param(key_cols[j], f'k_{j}')}" for j in range(i)]
        parts.append(f"{quote(col['name'])} > {cast_param(col, f'k_{i}')}")
        ors.append("(" + " AND ".join(parts) + ")")
    if len(key_cols) == 1:
        return ors[0][1:-1], params
    lead = key_cols[0]
    return f"{quote(lead['name'])} >= {cast_param(lead, 'k_0')} AND ({' OR '.join(ors)})", params


def _col(table_cfg: dict, name: str) -> dict:
    for c in table_cfg["columns"]:
        if c["name"] == name:
            return c
    raise KeyError(f"column {name} not in plan for {table_cfg['schema']}.{table_cfg['name']}")


def filter_conditions(table_cfg: dict, low=None, high=None) -> tuple[list[str], dict]:
    """WHERE conditions (ANDed) for the table's mode: cutoff (full), where /
    date range (partial), watermark window (incremental)."""
    conds, params = [], {}
    f = table_cfg.get("filters") or {}

    cutoff = f.get("cutoff")
    if cutoff:
        col = _col(table_cfg, cutoff["column"])
        value = str(cutoff["value"]).strip()
        if len(value) == 10:            # date only: through the END of that day
            day = datetime.date.fromisoformat(value) + datetime.timedelta(days=1)
            bound = day if base_type(col["data_type"]) == "date" else datetime.datetime.combine(day, datetime.time.min)
            conds.append(f"{quote(col['name'])} < {cast_param(col, 'cutoff')}")
        else:
            bound = coerce_text(col, value)
            conds.append(f"{quote(col['name'])} <= {cast_param(col, 'cutoff')}")
        params["cutoff"] = bind_value(bound)

    if f.get("where"):
        conds.append(str(f["where"]))

    dr = f.get("date_range")
    if dr:
        col = _col(table_cfg, dr["column"])
        if dr.get("from"):
            conds.append(f"{quote(col['name'])} >= {cast_param(col, 'd_from')}")
            params["d_from"] = bind_value(coerce_text(col, dr["from"]))
        if dr.get("to"):
            conds.append(f"{quote(col['name'])} < {cast_param(col, 'd_to')}")
            params["d_to"] = bind_value(coerce_text(col, dr["to"]))

    wm = table_cfg.get("watermark") if table_cfg.get("mode") == "incremental" else None
    if wm:
        col = _col(table_cfg, wm["column"])
        q = quote(col["name"])
        if wm["kind"] == "rowversion":
            if low is not None:
                conds.append(f"{q} > {cast_param(col, 'wm_low')}")
                params["wm_low"] = low
            if high is not None:
                conds.append(f"{q} < {cast_param(col, 'wm_high')}")
                params["wm_high"] = high
        else:
            if low is not None:
                conds.append(f"{q} > {cast_param(col, 'wm_low')}")
                params["wm_low"] = bind_value(low)
            if high is not None:
                if low is None:                # first run: also take rows with no watermark yet
                    conds.append(f"({q} <= {cast_param(col, 'wm_high')} OR {q} IS NULL)")
                else:
                    conds.append(f"{q} <= {cast_param(col, 'wm_high')}")
                params["wm_high"] = bind_value(high)
    return conds, params


def build_page_query(table_cfg: dict, read_from: tuple[str, str], batch_size: int,
                     conditions: list[str], last_key: dict | None) -> tuple[str, dict]:
    key_cols = [_col(table_cfg, k) for k in table_cfg["key"]["columns"]]
    ks_sql, ks_params = keyset_condition(key_cols, last_key)
    conds = list(conditions) + ([ks_sql] if ks_sql else [])
    where = (" WHERE " + " AND ".join(f"({c})" for c in conds)) if conds else ""
    order = ", ".join(quote(c["name"]) for c in key_cols)
    sql = (f"SELECT TOP ({int(batch_size)}) {select_list(table_cfg['columns'])} "
           f"FROM {table_ref(*read_from)}{where} ORDER BY {order}")
    return sql, ks_params


def build_stream_query(table_cfg: dict, conditions: list[str]) -> str:
    where = (" WHERE " + " AND ".join(f"({c})" for c in conditions)) if conditions else ""
    return f"SELECT {select_list(table_cfg['columns'])} FROM {table_ref(table_cfg['schema'], table_cfg['name'])}{where}"


# -------------------------------------------------------------- extractor --
class SqlExtractor:
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

    def read_from(self, table_cfg: dict) -> tuple[str, str]:
        if table_cfg["strategy"] == "shadow":
            return table_cfg["schema"], shadow_table_name(table_cfg["name"])
        return table_cfg["schema"], table_cfg["name"]

    def _fetch(self, sql: str, params: dict) -> list[tuple]:
        with self._get_engine().connect() as conn:
            return [tuple(r) for r in conn.execute(text(sql), params).fetchall()]

    def _scalar(self, sql: str, params: dict | None = None):
        with self._get_engine().connect() as conn:
            return conn.execute(text(sql), params or {}).scalar()

    # ---- reading -----------------------------------------------------------
    def iter_keyset(self, table_cfg: dict, batch_size: int, conditions: list[str], params: dict,
                    resume_after: dict | None = None):
        """Yields (rows, last_key_dict) per page."""
        key_names = table_cfg["key"]["columns"]
        names = [c["name"] for c in table_cfg["columns"]]
        positions = [names.index(k) for k in key_names]
        read_from = self.read_from(table_cfg)
        last = resume_after
        while True:
            sql, ks_params = build_page_query(table_cfg, read_from, batch_size, conditions, last)
            rows = self._fetch(sql, {**params, **ks_params})
            if not rows:
                return
            last = {k: rows[-1][p] for k, p in zip(key_names, positions)}
            yield rows, last
            if len(rows) < batch_size:
                return

    def iter_stream(self, table_cfg: dict, batch_size: int, conditions: list[str], params: dict):
        """Yields row lists from one forward-only query."""
        sql = build_stream_query(table_cfg, conditions)
        with self._get_engine().connect() as conn:
            result = conn.execution_options(stream_results=True).execute(text(sql), params)
            while True:
                rows = result.fetchmany(batch_size)
                if not rows:
                    return
                yield [tuple(r) for r in rows]

    def high_watermark(self, table_cfg: dict):
        """Upper bound of this incremental run, captured BEFORE reading, so rows
        changed during the run are picked up next time instead of half now."""
        wm = table_cfg["watermark"]
        if wm["kind"] == "rowversion":
            return self._scalar("SELECT CONVERT(VARCHAR(18), MIN_ACTIVE_ROWVERSION(), 1)")
        col = _col(table_cfg, wm["column"])
        return self._scalar(f"SELECT MAX({col['select_expr']}) FROM {table_ref(table_cfg['schema'], table_cfg['name'])}")

    def count_rows(self, table_cfg: dict, conditions: list[str], params: dict) -> int:
        where = (" WHERE " + " AND ".join(f"({c})" for c in conditions)) if conditions else ""
        return int(self._scalar(f"SELECT COUNT_BIG(*) FROM {table_ref(table_cfg['schema'], table_cfg['name'])}{where}",
                                params) or 0)

    # ---- shadow table (no_pk_strategy: shadow_table) ----------------------------
    def ensure_shadow(self, table_cfg: dict, rebuild: bool) -> None:
        schema, shadow = self.read_from(table_cfg)
        key_col = table_cfg["key"]["columns"][0]
        with self._get_engine().connect() as conn:
            exists = conn.execute(text("SELECT 1 FROM sys.tables t JOIN sys.schemas s ON s.schema_id = t.schema_id "
                                       "WHERE s.name = :s AND t.name = :t"), {"s": schema, "t": shadow}).first()
            if exists and not rebuild:
                LOG.info("Reusing shadow table %s", table_ref(schema, shadow))
                return
            if exists:
                conn.execute(text(f"DROP TABLE {table_ref(schema, shadow)}"))
            LOG.info("Materialising shadow table %s (no key on %s.%s)", table_ref(schema, shadow),
                     table_cfg["schema"], table_cfg["name"])
            conn.execute(text(
                f"SELECT *, CAST(ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS BIGINT) AS {quote(key_col)} "
                f"INTO {table_ref(schema, shadow)} FROM {table_ref(table_cfg['schema'], table_cfg['name'])}"))
            conn.execute(text(f"CREATE UNIQUE CLUSTERED INDEX {quote('IX_' + shadow[-100:])} "
                              f"ON {table_ref(schema, shadow)} ({quote(key_col)})"))
            conn.commit()

    def drop_shadow(self, table_cfg: dict) -> None:
        schema, shadow = self.read_from(table_cfg)
        with self._get_engine().connect() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS {table_ref(schema, shadow)}"))
            conn.commit()
