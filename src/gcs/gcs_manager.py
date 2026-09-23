"""
gcs_manager.py

Verifies that a table's Parquet files actually landed in GCS after
the STS transfer. Files are looked up under
parquet/<schema_name>/<table_name>/ — matching the schema-scoped
layout sts_client.py transfers into and bq_merge.py loads from (see
src/bigquery/dataset_naming.py for why the schema is kept as its own
path segment throughout the pipeline).

Requires: google-cloud-storage
"""
import time

from google.cloud import storage


class GcsManager:
    def __init__(self, config: dict):
        self.bucket_name = config["gcp"]["gcs_bucket"]
        self.client = storage.Client(project=config["gcp"]["project_id"])

    def overwrite_batch_file(self, local_path: str, table_name: str, schema_name: str,
                              batch_index: int) -> str:
        """Uploads local_path straight to GCS over the exact object a
        batch already landed in via the Azure -> STS transfer, at
        parquet/<schema_name>/<table_name>/<table_name>_part####.parquet.

        Used by table_pipeline.py's post-processing step (stage='gcs',
        synthetic-key tables) to replace the transferred file with a
        version that's had the internal source_row_id column stripped
        (see parquet_writer.remove_column) — going straight to GCS
        rather than re-running the whole Azure-upload + STS-transfer
        hop again for a column removal.
        """
        blob_name = f"parquet/{schema_name}/{table_name}/{table_name}_part{batch_index:04d}.parquet"
        print(f"[gcs_manager] Overwriting gs://{self.bucket_name}/{blob_name} "
              f"with cleaned copy of {local_path} ...")
        bucket = self.client.bucket(self.bucket_name)
        bucket.blob(blob_name).upload_from_filename(local_path)
        print(f"[gcs_manager] Overwrite complete: gs://{self.bucket_name}/{blob_name}")
        return blob_name

    def list_files(self, table_name: str, schema_name: str) -> list[str]:
        prefix = f"parquet/{schema_name}/{table_name}/"
        blobs = self.client.list_blobs(self.bucket_name, prefix=prefix)
        return [b.name for b in blobs]

    def files_exist(self, table_name: str, schema_name: str) -> bool:
        """Kept for backwards compatibility. NOTE: this only checks that
        *some* file exists for the table — once earlier batches have
        landed, this is always True and can't catch a specific batch's
        file failing to transfer. Use batch_file_exists() instead for
        anything that's about to load one specific batch."""
        return len(self.list_files(table_name, schema_name)) > 0

    def batch_file_exists(self, table_name: str, schema_name: str, batch_index: int,
                           retries: int = 3, retry_delay_seconds: int = 5) -> bool:
        """Checks that THIS batch's exact file exists in GCS — the file
        load_batch_to_staging() is about to point BigQuery at. Retries a
        few times with a short delay, since an STS operation can report
        done() a moment before the object is listable/gettable.
        """
        blob_name = f"parquet/{schema_name}/{table_name}/{table_name}_part{batch_index:04d}.parquet"
        bucket = self.client.bucket(self.bucket_name)
        print(f"[gcs_manager] Verifying gs://{self.bucket_name}/{blob_name} exists ...")

        for attempt in range(1, retries + 1):
            if bucket.blob(blob_name).exists(self.client):
                print(f"[gcs_manager] Confirmed: gs://{self.bucket_name}/{blob_name}")
                return True
            if attempt < retries:
                print(f"[gcs_manager] Not found yet (attempt {attempt}/{retries}), "
                      f"retrying in {retry_delay_seconds}s ...")
                time.sleep(retry_delay_seconds)

        print(f"[gcs_manager] MISSING after {retries} attempts: gs://{self.bucket_name}/{blob_name}")
        return False