resource "azurerm_virtual_network" "migration_vnet" {
  name                = "migration-vnet"
  address_space       = ["10.20.0.0/16"]
  location            = var.location
  resource_group_name = var.resource_group_name
}

resource "azurerm_subnet" "migration_subnet" {
  name                 = "migration-subnet"
  resource_group_name  = var.resource_group_name
  virtual_network_name = azurerm_virtual_network.migration_vnet.name
  address_prefixes     = ["10.20.1.0/24"]
}
