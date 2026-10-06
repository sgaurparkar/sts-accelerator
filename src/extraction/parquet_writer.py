"""
parquet_writer.py

Writes one extracted page/batch (see src/extraction/sql_extractor.py's
keyset pagination) to its own compressed Parquet file — this is the
step that gives you the real cost saving (smaller files = less Azure
egress, less GCS storage), and keeps each batch independently
loadable/mergeable so a failure doesn't force redoing the whole table.

Files are written under <output_dir>/<schema_name>/<table_name>_part####.parquet
so two tables with the same name in different source schemas (e.g.
dbo.orders and sales.orders) never overwrite each other locally,
mirroring the schema-scoped layout used in Azure/GCS/BigQuery — see
src/bigquery/dataset_naming.py.

DECIMAL-FAMILY COLUMNS: pandas has no fixed-point dtype, so
pd.read_sql_query (src/extraction/sql_extractor.py) reads SQL Server
DECIMAL/NUMERIC/MONEY/SMALLMONEY columns into plain float64 — and
df.to_parquet() would then write them with Parquet's physical DOUBLE
type. But every such column's BigQuery target/staging table is created
as NUMERIC (src/planner/type_mapper.py), and BigQuery's Parquet loader
does not support converting a physical DOUBLE into NUMERIC at all — it
fails the load outright asking for a DECIMAL logical type. So before
writing, we convert those columns' values to Python Decimal at the
source's own scale (from src/discovery/schema_reader.py); pyarrow
infers a proper decimal128 Parquet column from Decimal objects, which
BigQuery can load straight into NUMERIC.
"""
import os
from decimal import Decimal, InvalidOperation

import pandas as pd

# SQL Server source types that map to BigQuery NUMERIC (see
# src/planner/type_mapper.py's MSSQL_TO_BQ) and therefore need to be
# written as Parquet DECIMAL, not DOUBLE.
_DECIMAL_SOURCE_TYPES = {"decimal", "numeric", "money", "smallmoney"}

# BigQuery NUMERIC's own default scale — used only as a last-resort
# fallback if SQL Server didn't report a scale for some reason.
_DEFAULT_SCALE = 9


def _decimal_column_scales(columns: list[dict] | None) -> dict[str, int]:
    """{column_name: scale} for every column whose source type is
    decimal-family, from the precision/scale schema_reader.py now
    captures off INFORMATION_SCHEMA.COLUMNS."""
    if not columns:
        return {}
    scales = {}
    for col in columns:
        source_type = str(col.get("source_type", ""))
        key = source_type.split("(")[0].strip().lower()
        if key in _DECIMAL_SOURCE_TYPES:
            scale = col.get("scale")
            scales[col["name"]] = scale if scale is not None else _DEFAULT_SCALE
    return scales


def _to_fixed_decimal(value, scale: int):
    """Converts one cell to a Decimal quantized to `scale` places, or
    None for a missing value. Values arrive here as float64 (already
    lossy relative to the original SQL Server DECIMAL), so this fixes
    the column's Parquet *type* going forward — it can't recover
    precision the float64 round-trip already dropped upstream."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    quantum = Decimal(1).scaleb(-scale)
    try:
        return Decimal(str(value)).quantize(quantum)
    except InvalidOperation:
        return Decimal(value).quantize(quantum)


def write_parquet(df: pd.DataFrame, table_name: str, schema_name: str, batch_index: int,
                   columns: list[dict] | None = None,
                   output_dir: str = "data/parquet") -> str:
    schema_dir = os.path.join(output_dir, schema_name)
    os.makedirs(schema_dir, exist_ok=True)
    filename = f"{table_name}_part{batch_index:04d}.parquet"
    path = os.path.join(schema_dir, filename)

    decimal_scales = _decimal_column_scales(columns)
    for col_name, scale in decimal_scales.items():
        if col_name in df.columns:
            df[col_name] = df[col_name].apply(lambda v, s=scale: _to_fixed_decimal(v, s))

    print(f"[parquet_writer] Writing {len(df):,} row(s) to {path} ...")
    df.to_parquet(path, compression="snappy", index=False)
    print(f"[parquet_writer] Wrote {path} ({os.path.getsize(path):,} bytes)")
    return path


def remove_column(path: str, column_name: str) -> str:
    """Rewrites an already-written Parquet file in place with one column
    dropped — used to strip the internal `source_row_id` synthetic-key
    column (see table_planner.py / sql_extractor.py) out of a table's
    batch files once every batch has been processed and stage='gcs'
    stopped the pipeline before BigQuery (which would otherwise have
    been the thing to drop it, via BqMerge.drop_synthetic_column). A
    no-op if the column isn't present, so it's always safe to call.

    Any decimal-family columns round-trip through pandas/pyarrow as
    Decimal objects (object dtype), so re-writing here preserves the
    DECIMAL Parquet type write_parquet established above — nothing
    decimal-specific needs to happen in this function.
    """
    if not os.path.exists(path):
        raise FileNotFoundError(f"Cannot strip column '{column_name}': {path} does not exist")

    df = pd.read_parquet(path)
    if column_name not in df.columns:
        print(f"[parquet_writer] '{column_name}' already absent from {path} — nothing to strip")
        return path

    print(f"[parquet_writer] Stripping internal column '{column_name}' from {path} ...")
    df = df.drop(columns=[column_name])
    df.to_parquet(path, compression="snappy", index=False)
    print(f"[parquet_writer] Rewrote {path} without '{column_name}' "
          f"({os.path.getsize(path):,} bytes, {len(df.columns)} column(s) remain)")
    return path