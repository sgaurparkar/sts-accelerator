"""
type_mapper.py — SQL Server column -> everything the pipeline needs for it.

For every source column this decides, in one place:

  select_expr   what the extraction SELECT reads. Types the ODBC driver can't
                return (sql_variant, datetimeoffset) or returns as opaque bytes
                (geography, geometry, hierarchyid, xml) are converted in SQL
                Server, so nothing crashes and nothing lands in the wrong type.
  staging_type  BigQuery type of the column in the staging table (what the
                Parquet file holds)
  target_type   BigQuery type in the final table
  merge_expr    how staging is converted to target inside the MERGE
                (only DATETIME needs a cast — see below)
  arrow         pyarrow type the Parquet column is written with
  decl          T-SQL type declaration, used to CAST keyset / watermark
                parameters to the column's exact type, so comparisons stay
                index seeks and datetime values round-trip exactly

datetime / datetime2 / smalldatetime have no time zone. Loading them into a
BigQuery TIMESTAMP silently declared them UTC; they now land in DATETIME
(types.datetime_as: DATETIME, the default) and keep their wall-clock value.
Parquet carries them as a zone-less timestamp, staged as TIMESTAMP, and the
MERGE casts TIMESTAMP -> DATETIME, which is exact (no zone shift).
datetimeoffset is converted to UTC in SQL Server and lands in TIMESTAMP.
"""
import pyarrow as pa

INT_TYPES = {"int", "bigint", "smallint", "tinyint"}
DECIMAL_TYPES = {"decimal", "numeric", "money", "smallmoney"}
FLOAT_TYPES = {"float", "real"}
STRING_TYPES = {"varchar", "nvarchar", "char", "nchar", "text", "ntext", "sysname"}
BINARY_TYPES = {"varbinary", "binary", "image", "timestamp", "rowversion"}
NAIVE_DATETIME_TYPES = {"datetime", "datetime2", "smalldatetime"}
TEMPORAL_TYPES = NAIVE_DATETIME_TYPES | {"date", "time", "datetimeoffset"}
WATERMARK_TYPES = TEMPORAL_TYPES | INT_TYPES | DECIMAL_TYPES | {"timestamp", "rowversion"}

# BigQuery NUMERIC = precision 38, scale 9 (29 integer digits)
BQ_NUMERIC_MAX_SCALE = 9
BQ_NUMERIC_MAX_INTEGER_DIGITS = 29

# BigQuery types that can be clustering columns
CLUSTERABLE = {"INT64", "STRING", "DATE", "DATETIME", "TIMESTAMP", "BOOL", "NUMERIC", "BIGNUMERIC"}


def base_type(data_type: str) -> str:
    return str(data_type or "").split("(")[0].strip().lower()


def quote(name: str) -> str:
    """T-SQL bracket quoting ([a]]b] for a]b)."""
    return "[" + str(name).replace("]", "]]") + "]"


def _decimal_ps(col: dict) -> tuple[int, int]:
    t = base_type(col["data_type"])
    if t == "money":
        return 19, 4
    if t == "smallmoney":
        return 10, 4
    p = int(col.get("precision") or 18)
    s = int(col.get("scale") or 0)
    return p, s


def decl(col: dict) -> str:
    """T-SQL declaration of the column's type, e.g. nvarchar(50), decimal(18,2), datetime2(7)."""
    t = base_type(col["data_type"])
    length = col.get("max_length")
    if t in ("varchar", "nvarchar", "varbinary"):
        return f"{t}(max)" if length in (-1, None) or int(length) < 0 else f"{t}({int(length)})"
    if t in ("char", "nchar", "binary"):
        return f"{t}({int(length or 1)})"
    if t in ("decimal", "numeric"):
        p, s = _decimal_ps(col)
        return f"{t}({p},{s})"
    if t in ("datetime2", "time", "datetimeoffset"):
        scale = col.get("datetime_precision")
        return f"{t}({int(scale)})" if scale is not None else t
    if t in ("timestamp", "rowversion"):
        return "binary(8)"
    if t == "sysname":
        return "nvarchar(128)"
    return t


def map_column(col: dict, types_cfg: dict | None = None) -> dict:
    """Returns the col dict extended with select_expr / staging_type / target_type /
    merge_expr / arrow / decl / kind. col needs name, data_type and (when relevant)
    max_length, precision, scale, datetime_precision."""
    cfg = types_cfg or {}
    datetime_as = str(cfg.get("datetime_as") or "DATETIME").upper()
    if datetime_as not in ("DATETIME", "TIMESTAMP"):
        raise ValueError(f'types.datetime_as must be DATETIME or TIMESTAMP, not "{datetime_as}"')

    t = base_type(col["data_type"])
    q = quote(col["name"])
    out = dict(col)
    out["decl"] = decl(col)
    select_expr = q
    merge_cast = None

    if t in INT_TYPES:
        kind, staging, arrow = "int", "INT64", pa.int64()
    elif t == "bit":
        kind, staging, arrow = "bool", "BOOL", pa.bool_()
    elif t in DECIMAL_TYPES:
        p, s = _decimal_ps(col)
        big = s > BQ_NUMERIC_MAX_SCALE or (p - s) > BQ_NUMERIC_MAX_INTEGER_DIGITS
        kind, staging = "decimal", ("BIGNUMERIC" if big else "NUMERIC")
        arrow = pa.decimal128(max(p, 1), s)
        out["precision"], out["scale"] = p, s
    elif t in FLOAT_TYPES:
        kind, staging, arrow = "float", "FLOAT64", pa.float64()
    elif t in STRING_TYPES:
        kind, staging, arrow = "string", "STRING", pa.string()
    elif t == "uniqueidentifier":
        kind, staging, arrow = "string", "STRING", pa.string()
    elif t in BINARY_TYPES:
        kind, staging, arrow = "bytes", "BYTES", pa.binary()
    elif t == "date":
        kind, staging, arrow = "date", "DATE", pa.date32()
    elif t == "time":
        kind, staging, arrow = "time", "TIME", pa.time64("us")
    elif t in NAIVE_DATETIME_TYPES:
        kind, staging, arrow = "datetime", "TIMESTAMP", pa.timestamp("us")
        if datetime_as == "DATETIME":
            merge_cast = "DATETIME"
    elif t == "datetimeoffset":
        kind, staging, arrow = "datetime", "TIMESTAMP", pa.timestamp("us")
        select_expr = f"CAST(SWITCHOFFSET({q}, '+00:00') AS DATETIME2(7))"
    elif t in ("geography", "geometry"):
        kind, staging, arrow = "string", "STRING", pa.string()       # WKT
        select_expr = f"{q}.STAsText()"
    elif t == "hierarchyid":
        kind, staging, arrow = "string", "STRING", pa.string()       # '/1/2/'
        select_expr = f"{q}.ToString()"
    elif t == "xml":
        kind, staging, arrow = "string", "STRING", pa.string()
        select_expr = f"CAST({q} AS NVARCHAR(MAX))"
    elif t == "sql_variant":
        kind, staging, arrow = "string", "STRING", pa.string()
        select_expr = f"CAST({q} AS NVARCHAR(4000))"
    else:                                   # anything unknown: text in SQL Server, never a crash here
        kind, staging, arrow = "string", "STRING", pa.string()
        select_expr = f"CONVERT(NVARCHAR(MAX), {q})"

    out.update({
        "kind": kind,
        "select_expr": select_expr,
        "staging_type": staging,
        "target_type": merge_cast or staging,
        "merge_cast": merge_cast,
        "arrow": arrow,
    })
    return out


def map_type(source_type: str, precision: int | None = None, scale: int | None = None,
             types_cfg: dict | None = None) -> str:
    """Target BigQuery type for one SQL Server type (convenience/back-compat)."""
    return map_column({"name": "c", "data_type": source_type, "precision": precision, "scale": scale},
                      types_cfg)["target_type"]


def normalize_bq_type(field_type: str) -> str:
    """BigQuery API legacy names -> standard SQL names."""
    t = str(field_type or "").upper()
    return {"INTEGER": "INT64", "FLOAT": "FLOAT64", "BOOLEAN": "BOOL"}.get(t, t)
