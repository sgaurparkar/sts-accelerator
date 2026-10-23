"""
transport.py — gets a table run's Parquet files into the GCS landing bucket,
ONE FILE AT A TIME: every file is in GCS (and verified) before the next one is
written, so local disk and Azure staging only ever hold one file.

  stage_file(path, rel_dir)   called for every file as soon as it is written
  land(rel_dir, names)        called once after the last file: checks every file
                              is in GCS and returns the wildcard URI for the load

transfer.mode
  sts     (default) local -> Azure Blob staging (same Azure region as the server)
          -> Storage Transfer -> GCS. Best when the extractor runs inside Azure.
          transfer.sts_unit
            file    (default) one Storage Transfer job per Parquet file, run and
                    verified before the next file; the staged blob is deleted
                    right after. Expect ~30-90 s of job overhead per file — use a
                    larger extraction.batch_size for big tables.
            folder  files are only staged while reading; ONE job copies the
                    whole run folder at the end (fewer jobs, faster for big tables)
  direct  local -> GCS upload straight from the worker, one file at a time. Best
          when the extractor runs in GCP (Cloud Shell / Composer / a GCE VM).
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
        self.sts_unit = str(cfg.get("sts_unit") or "file").lower()
        if self.sts_unit not in ("file", "folder"):
            raise ValueError(f'transfer.sts_unit "{self.sts_unit}" — use file or folder.')
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

    @property
    def per_file(self) -> bool:
        """True when every file is in GCS as soon as stage_file returns."""
        return self.mode == "direct" or self.sts_unit == "file"

    def stage_file(self, local_path: str, rel_dir: str) -> str:
        """Moves ONE file towards GCS. direct: upload to GCS. sts/file: upload to Azure
        staging, run one Storage Transfer job for it, verify it in GCS, delete the
        staged blob. sts/folder: upload to Azure staging only. Returns the file name."""
        name = os.path.basename(local_path)
        if self.mode == "direct":
            self.gcs.upload_file(local_path, rel_dir + name)
        else:
            self._blob_uploader().upload_file(local_path, rel_dir + name)
            if self.sts_unit == "file":
                result = self._sts_manager().transfer_file(rel_dir, name, description=name)
                if result.get("status") != "SUCCESS":
                    raise TransferError(f"Storage Transfer of {rel_dir}{name} did not complete: {result}")
        if self.per_file:
            if name not in {n.rsplit("/", 1)[-1] for n in self.gcs.list_names(rel_dir + name)}:
                raise TransferError(f"gs://{self.bucket}/{rel_dir}{name} is missing after the transfer")
            if self.mode == "sts" and self.cleanup_azure:
                self._blob_uploader().delete_names([rel_dir + name])
            LOG.info("Transferred %s -> gs://%s/%s%s", name, self.bucket, rel_dir, name)
        return name

    def land(self, rel_dir: str, names: list[str], description: str = "") -> str:
        """Returns gs://bucket/<rel_dir>*.parquet once every staged file is in GCS."""
        if self.mode == "sts" and self.sts_unit == "folder":
            result = self._sts_manager().transfer_directory(rel_dir, len(names), description)
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

    def ship(self, local_files: list[str], rel_dir: str, description: str = "") -> str:
        """stage_file for every file, then land (kept for callers that have all files at once)."""
        names = [self.stage_file(p, rel_dir) for p in local_files]
        return self.land(rel_dir, names, description)

    # ---- resume support ------------------------------------------------------------
    def staged_names(self, rel_dir: str) -> set[str]:
        """File names already moved for a run folder (in GCS, or Azure staging for sts_unit: folder)."""
        names = self.gcs.list_names(rel_dir) if self.per_file else self._blob_uploader().list_names(rel_dir)
        return {n.rsplit("/", 1)[-1] for n in names}

    def landed_names(self, rel_dir: str) -> set[str]:
        return {n.rsplit("/", 1)[-1] for n in self.gcs.list_names(rel_dir)}

    def discard_from(self, rel_dir: str, first_part: int) -> int:
        """Deletes staged files whose part number is >= first_part: files a failed run
        uploaded after its last checkpoint, which the resumed run writes again."""
        from src.topology.paths import part_index
        stores = [self.gcs] + ([self._blob_uploader()] if self.mode == "sts" else [])
        total = 0
        for store in stores:
            stale = [n for n in store.list_names(rel_dir)
                     if part_index(n) is not None and part_index(n) >= first_part]
            if stale:
                LOG.info("Discarding %d file(s) past the checkpoint in %s", len(stale), rel_dir)
                store.delete_names(stale)
            total += len(stale)
        return total

    def after_load(self, rel_dir: str) -> None:
        if self.cleanup_gcs:
            self.gcs.delete_prefix(rel_dir)
