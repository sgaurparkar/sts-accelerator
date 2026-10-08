# Region-wise GCP resources — the same names/locations the pipeline derives from
# `regions:` in config/settings.yaml (src/config/region_resolver.py):
#
#   bucket           <landing_bucket_base>-<region>   e.g. migration-landing-bucket-east-us
#   control dataset  <bq_dataset>_<region>            e.g. migrated_data_east_us
#
# The per-schema DATA datasets (east_us_dbo, east_us_sales, ...) are created by the
# pipeline itself because schemas are discovered dynamically.
#
# Use EITHER this file OR let the pipeline create the buckets/datasets on its first
# run — not both. If the pipeline already created them, bring them under Terraform
# first, e.g.:
#   terraform import 'google_storage_bucket.regional_landing["east_us"]' migration-landing-bucket-east-us
#   terraform import 'google_bigquery_dataset.regional_control["east_us"]' projects/<project>/datasets/migrated_data_east_us
#
# Keys are the region slugs (lowercase, underscores) and values the GCP location —
# keep them in sync with regions.<name>.gcp_location in config/settings.yaml.

variable "regions" {
  description = "region slug => GCP location"
  type        = map(string)
  default = {
    north_central_us     = "us-central1"
    central_us           = "us-central1"
    brazil_south         = "southamerica-east1"
    germany_west_central = "europe-west3"
    east_us              = "us-east4"
    west_us_2            = "us-west1"
    southeast_asia       = "asia-southeast1"
    west_us              = "us-west2"
  }
}

variable "landing_bucket_base" {
  description = "Must equal gcp.gcs_bucket in config/settings.yaml"
  type        = string
  default     = "migration-landing-bucket"
}

# Storage Transfer Service's own service account — it writes the Azure data into the buckets.
data "google_storage_transfer_project_service_account" "sts" {
  project = var.project_id
}

resource "google_storage_bucket" "regional_landing" {
  for_each = var.regions

  name                        = "${var.landing_bucket_base}-${replace(each.key, "_", "-")}"
  location                    = each.value
  uniform_bucket_level_access = true
  force_destroy               = false

  # Only the transient Parquet landing files expire — never pipeline_logs/ (the audit trail).
  lifecycle_rule {
    condition {
      age            = 7
      matches_prefix = ["parquet/"]
    }
    action {
      type = "Delete"
    }
  }
}

resource "google_storage_bucket_iam_member" "sts_bucket_writer" {
  for_each = var.regions

  bucket = google_storage_bucket.regional_landing[each.key].name
  role   = "roles/storage.legacyBucketWriter"
  member = "serviceAccount:${data.google_storage_transfer_project_service_account.sts.email}"
}

resource "google_storage_bucket_iam_member" "sts_object_viewer" {
  for_each = var.regions

  bucket = google_storage_bucket.regional_landing[each.key].name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${data.google_storage_transfer_project_service_account.sts.email}"
}

resource "google_bigquery_dataset" "regional_control" {
  for_each = var.regions

  dataset_id = "${var.bq_dataset}_${each.key}"
  location   = each.value
}
