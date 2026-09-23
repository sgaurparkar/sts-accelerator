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
"""
import os
import pandas as pd


def write_parquet(df: pd.DataFrame, table_name: str, schema_name: str, batch_index: int,
                   output_dir: str = "data/parquet") -> str:
    schema_dir = os.path.join(output_dir, schema_name)
    os.makedirs(schema_dir, exist_ok=True)
    filename = f"{table_name}_part{batch_index:04d}.parquet"
    path = os.path.join(schema_dir, filename)
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