"""
sts_client.py — one-time Storage Transfer job per PARQUET FILE (transfer.sts_unit: file,
the default) or per run folder (sts_unit: folder):
Azure Blob <container>/<run folder>/<file>  ->  gs://<landing bucket>/<run folder>/<file>

The job is created in the target project (the resource group's project) and
deleted after it has run (sts.delete_jobs_after_run).
"""
from google.cloud import storage_transfer_v1 as storagetransfer

from src.azure.blob_uploader import storage_secret_suffixes
from src.common import log
from src.config.connection import storage_secret

LOG = log.get("sts")


class StsClient:
    def __init__(self, config: dict, client=None):
        self.project_id = config["gcp"]["project_id"]
        self.gcs_bucket = config["gcp"]["gcs_bucket"]
        self.azure_account = config["azure"]["storage_account"]
        self.azure_container = config["azure"]["container"]
        self.settings = config
        self.secret_suffixes = storage_secret_suffixes(config)
        self.client = client or storagetransfer.StorageTransferServiceClient()

    def build_job(self, rel_dir: str, description: str = "", include: list[str] | None = None):
        """include: object names (relative to rel_dir) to copy; None = the whole folder."""
        sas_token, _ = storage_secret("AZURE_SAS_TOKEN", self.settings)
        sas_token = sas_token.lstrip("?")          # STS wants the token without the leading "?"
        return storagetransfer.TransferJob(
            project_id=self.project_id,
            description=(f"sts-accelerator {description}")[:1024],
            transfer_spec=storagetransfer.TransferSpec(
                azure_blob_storage_data_source=storagetransfer.AzureBlobStorageData(
                    storage_account=self.azure_account,
                    container=self.azure_container,
                    path=rel_dir,
                    azure_credentials=storagetransfer.AzureCredentials(sas_token=sas_token),
                ),
                object_conditions=storagetransfer.ObjectConditions(include_prefixes=list(include)) if include else None,
                gcs_data_sink=storagetransfer.GcsData(bucket_name=self.gcs_bucket, path=rel_dir),
            ),
            status=storagetransfer.TransferJob.Status.ENABLED,
        )

    def create_job(self, rel_dir: str, description: str = "", include: list[str] | None = None) -> str:
        created = self.client.create_transfer_job(
            request={"transfer_job": self.build_job(rel_dir, description, include)})
        what = f"{rel_dir}{include[0]}" if include and len(include) == 1 else rel_dir
        LOG.info("Transfer job %s: azure://%s/%s/%s -> gs://%s/%s", created.name, self.azure_account,
                 self.azure_container, what, self.gcs_bucket, what)
        return created.name

    def delete_job(self, job_name: str) -> None:
        try:
            self.client.delete_transfer_job(request={"job_name": job_name, "project_id": self.project_id})
        except Exception as e:
            LOG.warning("Could not delete transfer job %s: %s", job_name, e)
