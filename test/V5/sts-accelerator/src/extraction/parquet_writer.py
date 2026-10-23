"""
parquet_writer.py — rows (tuples straight from pyodbc) -> one Parquet file with
an explicit Arrow schema built from the planned column types.

No pandas in between: pandas turned nullable integers into floats, decimals
into floats and datetimes into nanoseconds, which is what used to make
BigQuery reject loads. Here every column is written with exactly the type its
staging column has:

  INT64 -> int64            NUMERIC/BIGNUMERIC -> decimal128(p, s) at the source scale
  FLOAT64 -> float64        STRING -> string          BYTES -> binary
  BOOL -> bool              DATE -> date32            TIME -> time64[us]
  TIMESTAMP -> timestamp[us] (zone-less; datetimeoffset already converted to UTC)
"""
import datetime
import decimal
import os

import pyarrow as pa
import pyarrow.parquet as pq


def arrow_type(col: dict) -> pa.DataType:
    kind = col["kind"]
    if kind == "int":
        return pa.int64()
    if kind == "bool":
        return pa.bool_()
    if kind == "decimal":
        return pa.decimal128(max(int(col.get("precision") or 38), 1), int(col.get("scale") or 0))
    if kind == "float":
        return pa.float64()
    if kind == "bytes":
        return pa.binary()
    if kind == "date":
        return pa.date32()
    if kind == "time":
        return pa.time64("us")
    if kind == "datetime":
        return pa.timestamp("us")
    return pa.string()


def arrow_schema(columns: list[dict]) -> pa.Schema:
    return pa.schema([pa.field(c["bq_name"], arrow_type(c), nullable=True) for c in columns])


def _converter(col: dict):
    kind = col["kind"]
    if kind == "int":
        return int
    if kind == "bool":
        return bool
    if kind == "float":
        return float
    if kind == "decimal":
        quantum = decimal.Decimal(1).scaleb(-int(col.get("scale") or 0))

        def to_dec(v):
            d = v if isinstance(v, decimal.Decimal) else decimal.Decimal(str(v))
            return d.quantize(quantum, rounding=decimal.ROUND_HALF_EVEN)
        return to_dec
    if kind == "bytes":
        return lambda v: bytes(v) if not isinstance(v, str) else v.encode("utf-8")
    if kind == "date":
        return lambda v: v.date() if isinstance(v, datetime.datetime) else v
    if kind == "datetime":
        def to_dt(v):
            if isinstance(v, datetime.datetime):
                if v.tzinfo is not None:
                    v = v.astimezone(datetime.timezone.utc).replace(tzinfo=None)
                return v
            if isinstance(v, datetime.date):
                return datetime.datetime.combine(v, datetime.time.min)
            return datetime.datetime.fromisoformat(str(v))
        return to_dt
    if kind == "time":
        return lambda v: v if isinstance(v, datetime.time) else datetime.time.fromisoformat(str(v))
    return lambda v: v if isinstance(v, str) else (str(v).upper() if v.__class__.__name__ == "UUID" else str(v))


def rows_to_table(rows: list[tuple], columns: list[dict]) -> pa.Table:
    schema = arrow_schema(columns)
    arrays = []
    for i, col in enumerate(columns):
        conv = _converter(col)
        try:
            values = [None if r[i] is None else conv(r[i]) for r in rows]
            arrays.append(pa.array(values, type=schema.field(i).type))
        except Exception as e:
            sample = next((r[i] for r in rows if r[i] is not None), None)
            raise ValueError(f"Column {col['name']} ({col['data_type']} -> {col['staging_type']}): cannot convert "
                             f"value {sample!r}: {e}") from e
    return pa.Table.from_arrays(arrays, schema=schema)


def write_rows(rows: list[tuple], columns: list[dict], path: str) -> tuple[str, int, int]:
    """Writes rows to path. Returns (path, rows, bytes)."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    table = rows_to_table(rows, columns)
    pq.write_table(table, path, compression="snappy")
    return path, table.num_rows, os.path.getsize(path)
