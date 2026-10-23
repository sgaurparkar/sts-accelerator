
data "google_compute_network" "migration_vpc" {

  count = var.create_composer ? 1 : 0

  name  = "migration-vpc"

}
 
data "google_compute_subnetwork" "composer" {

  count  = var.create_composer ? 1 : 0

  name   = var.composer_subnet

  region = var.region

}

