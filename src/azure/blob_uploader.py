"""
blob_uploader.py

Uploads a local Parquet file to the Azure Blob container, using the
connection string from .env (never hardcoded).

Blobs are laid out as <schema_name>/<table_name>/<filename> — the
source schema is the first path segment — so tables with the same
name in two different SQL Server schemas (e.g. dbo.orders and
sales.orders) never collide in the same container path, and the
layout in Azure mirrors the per-schema BigQuery dataset split (see
src/bigquery/dataset_naming.py) and the GCS layout the STS transfer
lands them in (see src/sts/sts_client.py).

Large batches (e.g. 20 lakh+ rows) produce large Parquet files, and a
single slow moment on a constrained network (Cloud Shell's egress in
particular) is enough to trip the SDK's default timeouts mid-transfer
— that's what ('Connection aborted.', TimeoutError('The write
operation timed out')) means. Three things fix that:

  1. Generous connection/read timeouts on the client itself, instead
     of the SDK defaults (which are quite short).
  2. Uploading in parallel blocks (max_concurrency + max_block_size)
     rather than one giant serial stream — each block is retried
     independently by the SDK if it stalls, instead of the whole
     multi-hundred-MB file failing and restarting from zero.
  3. Our own retry-with-backoff wrapper around the whole upload, as a
     belt-and-suspenders layer for anything the SDK's own retries
     don't absorb (e.g. a full connection reset).

Requires: azure-storage-blob
  pip install azure-storage-blob
"""
import os
import time
from azure.storage.blob import BlobServiceClient


class BlobUploader:
    def __init__(self, config: dict):
        self.container_name = config["azure"]["container"]
        conn_str = os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
        if not conn_str:
            raise RuntimeError(
                "AZURE_STORAGE_CONNECTION_STRING not set — check your .env file"
            )
        upload_cfg = config.get("azure", {}).get("upload", {})
        self.connection_timeout = upload_cfg.get("connection_timeout_seconds", 300)
        self.read_timeout = upload_cfg.get("read_timeout_seconds", 300)
        self.max_block_size = upload_cfg.get("max_block_size_bytes", 8 * 1024 * 1024)   # 8 MB blocks
        self.max_concurrency = upload_cfg.get("max_concurrency", 4)
        self.max_retries = upload_cfg.get("max_retries", 3)
        self.retry_backoff_seconds = upload_cfg.get("retry_backoff_seconds", 5)

        self.client = BlobServiceClient.from_connection_string(
            conn_str,
            connection_timeout=self.connection_timeout,
            read_timeout=self.read_timeout,
        )

    def upload_file(self, local_path: str, table_name: str, schema_name: str) -> str:
        """Uploads to <container>/<schema_name>/<table_name>/<filename>,
        returns the blob path. Retries the whole upload up to
        max_retries times with exponential backoff if it fails partway
        through — a fresh attempt re-opens the local file and re-sends
        from the start, since a partial block-based upload can't safely
        be resumed from an arbitrary byte offset without extra
        bookkeeping this pipeline doesn't need for batch-sized files."""
        filename = os.path.basename(local_path)
        blob_path = f"{schema_name}/{table_name}/{filename}"
        blob_client = self.client.get_blob_client(container=self.container_name, blob=blob_path)

        size_bytes = os.path.getsize(local_path)
        print(f"[blob_uploader] Uploading {local_path} ({size_bytes:,} bytes) "
              f"to azure://{self.container_name}/{blob_path} ...")

        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with open(local_path, "rb") as f:
                    blob_client.upload_blob(
                        f,
                        overwrite=True,
                        max_concurrency=self.max_concurrency,
                        timeout=self.read_timeout,
                    )
                print(f"[blob_uploader] Upload complete: azure://{self.container_name}/{blob_path}")
                return blob_path
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = self.retry_backoff_seconds * (2 ** (attempt - 1))
                    print(f"[blob_uploader] Upload attempt {attempt}/{self.max_retries} "
                          f"failed for {blob_path}: {e}. Retrying in {wait}s...")
                    time.sleep(wait)

        print(f"[blob_uploader] Upload FAILED for {blob_path} after {self.max_retries} attempts")
        raise RuntimeError(
            f"Upload of {blob_path} failed after {self.max_retries} attempts: {last_error}"
        ) from last_error

    def list_blobs(self, table_name: str, schema_name: str) -> list[str]:
        container_client = self.client.get_container_client(self.container_name)
        return [b.name for b in container_client.list_blobs(name_starts_with=f"{schema_name}/{table_name}/")]
