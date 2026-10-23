resource "azurerm_storage_account" "staging" {
  name                     = var.storage_account_name
  resource_group_name      = var.resource_group_name
  location                 = var.location
  account_tier             = "Standard"
  account_replication_type = "LRS"
}

resource "azurerm_storage_container" "migration_staging" {
  name                  = "migration-staging"
  storage_account_name  = azurerm_storage_account.staging.name
  container_access_type = "private"
}
