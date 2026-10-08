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

from google.api_core import exceptions as gexc
from google.cloud import storage

_ENSURED_BUCKETS: set[str] = set()   # buckets already verified in this process


class GcsManager:
    def __init__(self, config: dict):
        self.bucket_name = config["gcp"]["gcs_bucket"]
        self.location = config["gcp"].get("location")
        self.region_mode = bool(config.get("source", {}).get("region_slug"))
        self.client = storage.Client(project=config["gcp"]["project_id"])

    def ensure_bucket(self) -> None:
        """Region mode only: makes sure this region's landing bucket exists in the
        region's GCP location, creating it if needed.

          * existing bucket in the SAME location  -> left untouched
          * existing bucket in ANOTHER location   -> RuntimeError (BigQuery can't load
            from a bucket in a different location, and the failure it gives is confusing)
          * name owned by another project / no permission -> RuntimeError with the reason
          * two runs racing to create it          -> the loser just re-reads it

        New bucket => grant the Storage Transfer Service account write access once
        (see README "Choosing the source region"). Checked once per process.
        """
        if not self.region_mode or self.bucket_name in _ENSURED_BUCKETS:
            return
        bucket = self._lookup()
        if bucket is None:
            print(f"[gcs_manager] Creating bucket gs://{self.bucket_name} in {self.location} ...")
            try:
                bucket = self.client.create_bucket(self.client.bucket(self.bucket_name),
                                                   location=self.location)
            except gexc.Conflict:                      # created by a parallel run, or name taken
                bucket = self._lookup()
                if bucket is None:
                    raise RuntimeError(f"Bucket name gs://{self.bucket_name} is not available. Set "
                                       "regions.<name>.gcs_bucket (or gcp.gcs_bucket) to a unique name.")
            except gexc.Forbidden as e:
                raise RuntimeError(f"No permission to create gs://{self.bucket_name} "
                                   "(needs storage.buckets.create on the project).") from e
        existing = (getattr(bucket, "location", None) or "").lower()
        if self.location and existing and existing != self.location.lower():
            raise RuntimeError(
                f"gs://{self.bucket_name} already exists in {existing}, but this region is configured "
                f"for {self.location}. BigQuery needs the bucket and datasets in the same location — "
                "use a different bucket name (regions.<name>.gcs_bucket) or fix gcp_location.")
        _ENSURED_BUCKETS.add(self.bucket_name)

    def _lookup(self):
        try:
            return self.client.lookup_bucket(self.bucket_name)      # None when it doesn't exist
        except gexc.Forbidden as e:
            raise RuntimeError(f"Cannot access gs://{self.bucket_name}: the name may belong to another "
                               "project, or this account lacks storage.buckets.get.") from e

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