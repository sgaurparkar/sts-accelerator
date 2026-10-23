resource "azurerm_resource_group" "migration_rg" {
  name     = var.resource_group_name
  location = var.location
}

# NOTE: this references an EXISTING SQL Server/Database in most real
# migrations (you're moving data OUT of one that already exists) —
# included here mainly for spinning up a throwaway test server if
# you need one for practice runs.
resource "azurerm_mssql_server" "demo" {
  name                         = "migration-demo-sqlserver"
  resource_group_name          = azurerm_resource_group.migration_rg.name
  location                     = var.location
  version                      = "12.0"
  administrator_login          = var.sql_admin_username
  administrator_login_password = var.sql_admin_password
}

variable "resource_group_name" {
  type    = string
  default = "migration-accelerator-rg"
}

variable "location" {
  type    = string
  default = "Central India"
}

variable "storage_account_name" {
  type = string
}

variable "sql_admin_username" {
  type      = string
  sensitive = true
}

variable "sql_admin_password" {
  type      = string
  sensitive = true
}

provider "azurerm" {
  features {}
}

terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 3.0"
    }
  }
}
