# Composer is OFF by default for the POC (it takes ~25 minutes and costs money while idle).
# The POC runs the pipeline from a VM inside the same network (guide step 11).
# Turn it on later with:  terraform apply -var create_composer=true
resource "google_composer_environment" "migration_composer" {
  count  = var.create_composer ? 1 : 0
  name   = "migration-composer"
  region = var.region

  config {
    software_config {
      image_version = "composer-2-airflow-2"
    }

    # Composer must sit INSIDE migration-vpc-global (see vpc.tf), otherwise it cannot
    # reach the private Azure SQL / Blob addresses that arrive through the VPN tunnels.
    node_config {
      network    = data.google_compute_network.migration_vpc[0].id
      subnetwork = data.google_compute_subnetwork.composer[0].id
    }
  }
}

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east4"   # must be the region of var.composer_subnet
}

variable "create_composer" {
  type    = bool
  default = false
}

variable "composer_subnet" {
  type    = string
  default = "subnet-na-eastus"
}

variable "bq_dataset" {
  type    = string
  default = "migrated_data"
}

provider "google" {
  project = var.project_id
  region  = var.region
}

terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}
