"""
blob_uploader.py — Azure Blob staging for transfer.mode: sts.

Files land under <container>/<run folder>, the same relative path the
Storage Transfer job then copies to GCS. The staging account is picked per
Azure location (azure.storage_accounts_by_location) so India data stays in an
India account while staged; credentials are looked up per account, then per
location, then shared (templates in settings.yaml secrets.storage_name_templates):
  <RESOURCE_GROUP>_AZURE_STORAGE_CONNECTION_STRING / AZURE_STORAGE_CONNECTION_STRING_<ACCOUNT> /
  AZURE_STORAGE_CONNECTION_STRING_<LOCATION> / AZURE_STORAGE_CONNECTION_STRING
Staged blobs are deleted once the run's files have landed in GCS (cleanup.azure_staging).
"""
import os
import time

from src.common import log
from src.config.connection import storage_secret
from src.topology.naming import slug

LOG = log.get("blob")


def storage_secret_suffixes(config: dict) -> list[str]:
    """[<ACCOUNT>, <AZURE_LOCATION>] for regional_env lookups."""
    out = []
    for part in ((config.get("azure") or {}).get("storage_account"),
                 (config.get("source") or {}).get("azure_location")):
        s = slug(part).upper()
        if s and s not in out:
            out.append(s)
    return out


class BlobUploader:
    def __init__(self, config: dict, client=None):
        self.settings = config
        self.container_name = config["azure"]["container"]
        self.secret_suffixes = storage_secret_suffixes(config)
        upload_cfg = (config.get("azure") or {}).get("upload") or {}
        self.read_timeout = upload_cfg.get("read_timeout_seconds", 300)
        self.max_concurrency = upload_cfg.get("max_concurrency", 4)
        self.max_retries = upload_cfg.get("max_retries", 3)
        self.retry_backoff_seconds = upload_cfg.get("retry_backoff_seconds", 5)
        if client is not None:
            self.client = client
            return
        conn_str, _ = storage_secret("AZURE_STORAGE_CONNECTION_STRING", config)
        from azure.storage.blob import BlobServiceClient
        self.client = BlobServiceClient.from_connection_string(
            conn_str,
            connection_timeout=upload_cfg.get("connection_timeout_seconds", 300),
            read_timeout=self.read_timeout,
            max_block_size=upload_cfg.get("max_block_size_bytes", 8 * 1024 * 1024),
        )

    def upload_file(self, local_path: str, blob_path: str) -> str:
        blob_client = self.client.get_blob_client(container=self.container_name, blob=blob_path)
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                with open(local_path, "rb") as f:
                    blob_client.upload_blob(f, overwrite=True, max_concurrency=self.max_concurrency,
                                            timeout=self.read_timeout)
                return blob_path
            except Exception as e:
                last_error = e
                if attempt < self.max_retries:
                    wait = self.retry_backoff_seconds * (2 ** (attempt - 1))
                    LOG.warning("Upload %d/%d of %s failed: %s — retrying in %ss", attempt, self.max_retries,
                                blob_path, e, wait)
                    time.sleep(wait)
        raise RuntimeError(f"Upload of {blob_path} failed after {self.max_retries} attempts: {last_error}") \
            from last_error

    def upload_files(self, local_paths: list[str], rel_dir: str) -> list[str]:
        return [self.upload_file(p, rel_dir + os.path.basename(p)) for p in local_paths]

    def list_names(self, rel_dir: str) -> list[str]:
        container = self.client.get_container_client(self.container_name)
        return [b.name for b in container.list_blobs(name_starts_with=rel_dir)]

    def delete_names(self, names: list[str]) -> int:
        container = self.client.get_container_client(self.container_name)
        for name in names:
            try:
                container.delete_blob(name)
            except Exception as e:
                LOG.warning("Could not delete staged blob %s: %s", name, e)
        return len(names)

    def delete_prefix(self, rel_dir: str) -> int:
        container = self.client.get_container_client(self.container_name)
        names = [b.name for b in container.list_blobs(name_starts_with=rel_dir)]
        for name in names:
            try:
                container.delete_blob(name)
            except Exception as e:
                LOG.warning("Could not delete staged blob %s: %s", name, e)
        return len(names)
