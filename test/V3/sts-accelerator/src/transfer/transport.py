"""
transport.py — gets one chunk of Parquet files into the GCS landing bucket.

transfer.mode
  sts     (default) local -> Azure Blob staging (same Azure region as the server)
          -> ONE Storage Transfer job for the whole chunk folder -> GCS.
          Best when the extractor runs inside Azure, next to the SQL server.
  direct  local -> GCS upload straight from the worker. Best when the extractor
          runs in GCP (Cloud Shell / Composer / a GCE VM): the data crosses
          clouds once instead of three times, and there is no per-chunk
          transfer-job overhead.

Either way the chunk is verified in GCS (every file present) before it is
loaded, and the caller gets the wildcard URI to load in one BigQuery job.
"""
import os

from src.common import log

LOG = log.get("transport")


class TransferError(RuntimeError):
    pass


class Transport:
    def __init__(self, settings: dict, gcs=None, blob=None, sts=None):
        self.settings = settings
        cfg = settings.get("transfer") or {}
        self.mode = str(cfg.get("mode") or "sts").lower()
        if self.mode not in ("sts", "direct"):
            raise ValueError(f'transfer.mode "{self.mode}" — use sts or direct.')
        cleanup = settings.get("cleanup") or {}
        self.cleanup_azure = bool(cleanup.get("azure_staging", True))
        self.cleanup_gcs = bool(cleanup.get("gcs_after_load", False))
        self.bucket = settings["gcp"]["gcs_bucket"]
        if gcs is None:
            from src.gcs.gcs_manager import GcsManager
            gcs = GcsManager(settings)
        self.gcs = gcs
        self._blob, self._sts = blob, sts

    def _blob_uploader(self):
        if self._blob is None:
            from src.azure.blob_uploader import BlobUploader
            self._blob = BlobUploader(self.settings)
        return self._blob

    def _sts_manager(self):
        if self._sts is None:
            from src.sts.sts_job_manager import StsJobManager
            self._sts = StsJobManager(self.settings)
        return self._sts

    def ship(self, local_files: list[str], rel_dir: str, description: str = "") -> str:
        """Returns gs://bucket/<rel_dir>*.parquet once every file is in GCS."""
        names = [os.path.basename(p) for p in local_files]
        if self.mode == "direct":
            for path in local_files:
                self.gcs.upload_file(path, rel_dir + os.path.basename(path))
        else:
            self._blob_uploader().upload_files(local_files, rel_dir)
            result = self._sts_manager().transfer_directory(rel_dir, len(local_files), description)
            if result.get("status") != "SUCCESS":
                raise TransferError(f"Storage Transfer for {rel_dir} did not complete: {result}")
            if self.cleanup_azure:
                self._blob_uploader().delete_prefix(rel_dir)
        landed = {n.rsplit("/", 1)[-1] for n in self.gcs.list_names(rel_dir)}
        missing = [n for n in names if n not in landed]
        if missing:
            raise TransferError(f"{len(missing)} file(s) missing in gs://{self.bucket}/{rel_dir} after transfer: "
                                f"{missing[:5]}")
        return f"gs://{self.bucket}/{rel_dir}*.parquet"

    def after_load(self, rel_dir: str) -> None:
        if self.cleanup_gcs:
            self.gcs.delete_prefix(rel_dir)
