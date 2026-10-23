"""Fixtures shared by the offline tests: an inventory topology and a fake catalog."""
import copy

from src.topology.targets import build_migration_targets, settings_for_target

INVENTORY = [
    {"resource_group": "ResourceGroup1", "servers": [
        {"name": "SQLServer1", "location": "Central India", "databases": ["SalesDB"]},
        {"name": "SQLServer2", "location": "East US", "databases": ["HRDB"]}]},
    {"resource_group": "ResourceGroup2", "servers": [
        {"name": "SQLServer3", "location": "Central India", "databases": ["FinanceDB"]}]},
]


def base_settings(tmp_path=None, **extra) -> dict:
    s = {
        "azure_topology": {"source": "inventory", "inventory": copy.deepcopy(INVENTORY)},
        "source": {"type": "mssql"},
        "azure": {"storage_account": "stgacct", "container": "c"},
        "gcp": {}, "metadata": {},
        "extraction": {"batch_size": 2, "cutoff_date": None,
                       "local_dir": str(tmp_path / "data") if tmp_path else "data"},
        "transfer": {"mode": "direct"},
        "overrides_file": str(tmp_path / "overrides.yaml") if tmp_path else None,
        "plans_dir": str(tmp_path / "plans") if tmp_path else None,
    }
    for k, v in extra.items():
        s[k] = v
    return s


def resolved(settings: dict, index: int = 0) -> dict:
    return settings_for_target(settings, build_migration_targets(settings)[index])


def col(name, data_type, **kw):
    out = {"name": name, "data_type": data_type, "max_length": kw.pop("max_length", None),
           "precision": kw.pop("precision", None), "scale": kw.pop("scale", None),
           "datetime_precision": kw.pop("datetime_precision", None), "is_nullable": kw.pop("is_nullable", True),
           "is_identity": kw.pop("is_identity", False), "is_computed": False, "ordinal": 0}
    out.update(kw)
    return out


def entry(schema, name, columns, pk=None, unique=None, rows=100):
    return {"schema": schema, "name": name, "columns": columns, "primary_key": pk or [],
            "unique_keys": unique or [], "row_estimate": rows}


ORDERS = entry("dbo", "Orders", [
    col("OrderId", "int", is_identity=True, is_nullable=False),
    col("Customer", "nvarchar", max_length=100),
    col("Amount", "decimal", precision=18, scale=2),
    col("ModifiedDate", "datetime2", datetime_precision=7),
    col("updatedAt", "datetime2", datetime_precision=7),
], pk=["OrderId"], rows=5000)

CUSTOMERS = entry("dbo", "Customers", [
    col("CustomerId", "uniqueidentifier", is_nullable=False),
    col("Name", "varchar", max_length=50),
    col("RowVer", "timestamp"),
], pk=["CustomerId"], rows=300)

HEAP = entry("dbo", "EventLog", [col("Message", "nvarchar", max_length=-1), col("At", "datetime")], rows=900)

SALES_ORDERS = entry("sales", "Orders", [col("Id", "bigint", is_nullable=False), col("Total", "money")],
                     unique=[["Id"]], rows=10)

CATALOG = {(e["schema"], e["name"]): e for e in (ORDERS, CUSTOMERS, HEAP, SALES_ORDERS)}


class FakeReader:
    """Stands in for CatalogReader: same catalog for every database."""
    calls = 0

    def __init__(self, settings, catalog=None):
        self.catalog = catalog or CATALOG

    def read(self, schemas=None, table=None):
        FakeReader.calls += 1
        if table:
            return {k: v for k, v in self.catalog.items() if k == table}
        return {k: copy.deepcopy(v) for k, v in self.catalog.items() if not schemas or k[0] in schemas}
