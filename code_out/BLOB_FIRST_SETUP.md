# Blob-first setup (Parquet -> Azure Blob -> GCS)
Settings already set in config/settings.yaml: mirror_to_blob: true, blob_first: true, container parquet-backup,
storage_account per region (East US azurergnavma101807140, West US 2 stgnawest47913, Germany WC stgemea12644),
network.require_private: true. Connection strings go in .env (AZURE_STORAGE_CONNECTION_STRING_<REGION>).
Blob write fails -> batch fails, GCS untouched, rerun resumes from checkpoint.
Check:  python3 verify_blob_vs_gcs.py --bucket sts-accelerator-landing-east-us --region-slug east_us --all-tables
Fix:    add --repair --direction blob-to-gcs
