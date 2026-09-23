"""
sts_client.py

Thin wrapper around the Storage Transfer Service API — creates a
one-time transfer job from an Azure Blob folder to a GCS bucket
folder for a specific table.

Both the Azure source path and the GCS sink path are scoped by
<schema_name>/<table_name>/, matching the layout blob_uploader.py
writes to and bq_merge.py reads back from — see
src/bigquery/dataset_naming.py for why the source schema is kept as
its own segment throughout the pipeline instead of being flattened.

Requires: google-cloud-storage-transfer
  pip install google-cloud-storage-transfer
"""
import os
import datetime
from google.cloud import storage_transfer_v1 as storagetransfer


class StsClient:
    def __init__(self, config: dict):
        self.project_id = config["gcp"]["project_id"]
        self.gcs_bucket = config["gcp"]["gcs_bucket"]
        self.azure_account = config["azure"]["storage_account"]
        self.azure_container = config["azure"]["container"]
        self.client = storagetransfer.StorageTransferServiceClient()

    def create_job_for_table(self, table_name: str, schema_name: str) -> str:
        sas_token = os.environ.get("AZURE_SAS_TOKEN")
        if not sas_token:
            raise RuntimeError("AZURE_SAS_TOKEN not set — check your .env file")

        azure_path = f"{schema_name}/{table_name}/"
        gcs_path = f"parquet/{schema_name}/{table_name}/"

        print(f"[sts_client] Creating transfer job for {schema_name}.{table_name}: "
              f"azure://{self.azure_container}/{azure_path} "
              f"-> gs://{self.gcs_bucket}/{gcs_path}")

        transfer_job = storagetransfer.TransferJob(
            project_id=self.project_id,
            transfer_spec=storagetransfer.TransferSpec(
                azure_blob_storage_data_source=storagetransfer.AzureBlobStorageData(
                    storage_account=self.azure_account,
                    container=self.azure_container,
                    path=azure_path,
                    azure_credentials=storagetransfer.AzureCredentials(sas_token=sas_token),
                ),
                gcs_data_sink=storagetransfer.GcsData(
                    bucket_name=self.gcs_bucket,
                    path=gcs_path,
                ),
            ),
            status=storagetransfer.TransferJob.Status.ENABLED,
        )
        created = self.client.create_transfer_job(
            request={"transfer_job": transfer_job}
        )
        print(f"[sts_client] Transfer job created: {created.name}")
        return created.name  # e.g. "transferJobs/12345"
