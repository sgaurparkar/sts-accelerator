"""
models.py — the two records that describe WHAT gets migrated WHERE.

AzureSqlDatabase   one database as found in Azure (resource group, server,
                   database, Azure location)
MigrationTarget    that database plus every GCP name derived for it (project,
                   location, dataset, landing bucket, control dataset, paths)

Both are plain, JSON-serialisable dataclasses so they can travel through
Airflow XCom (to_dict / from_dict).
"""
from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class AzureSqlDatabase:
    resource_group: str          # as Azure writes it, e.g. "ResourceGroup1"
    server: str                  # short server name, e.g. "sqlserver1"
    server_fqdn: str             # e.g. "sqlserver1.database.windows.net"
    database: str                # e.g. "SalesDB"
    azure_location: str          # normalised programmatic name, e.g. "centralindia"
    subscription_id: str | None = None
    status: str | None = None    # ARM database status (Online, Paused, ...) when known

    @property
    def key(self) -> str:
        return f"{self.resource_group}/{self.server}/{self.database}"


@dataclass(frozen=True)
class MigrationTarget:
    source: AzureSqlDatabase
    gcp_project_id: str          # from the resource group      -> resourcegroup1
    gcp_project_display_name: str
    gcp_location: str            # from the Azure location      -> asia-south1
    dataset_id: str              # from server + database       -> sqlserver1_salesdb
    control_dataset_id: str      # per project + location       -> migration_control_asia_south1
    landing_bucket: str          # per project + location       -> resourcegroup1-landing-asia-south1
    object_prefix: str           # rg/server/db path segment    -> resourcegroup1/sqlserver1/salesdb

    @property
    def key(self) -> str:
        return self.source.key

    def describe(self) -> str:
        s = self.source
        return (f"{s.resource_group}/{s.server}/{s.database} ({s.azure_location}) -> "
                f"{self.gcp_project_id}.{self.dataset_id} ({self.gcp_location})")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "MigrationTarget":
        data = dict(data)
        data["source"] = AzureSqlDatabase(**data["source"])
        return cls(**data)
