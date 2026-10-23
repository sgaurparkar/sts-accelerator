"""Offline tests for the Azure -> GCP mapping:  python -m pytest tests/test_topology.py"""
import copy
from types import SimpleNamespace

import pytest

from src.topology import naming
from src.topology.azure_discovery import AzureTopologyDiscovery, TopologyError
from src.topology.location_mapping import LocationMappingError, resolve_gcp_location
from src.topology.models import AzureSqlDatabase, MigrationTarget
from src.topology.paths import batch_filename, local_dir, run_rel_dir, run_tag
from src.topology.targets import build_migration_targets, format_topology, settings_for_target

# The architecture from the brief.
INVENTORY = [
    {"resource_group": "ResourceGroup1", "servers": [
        {"name": "SQLServer1", "location": "Central India", "databases": ["SalesDB"]},
        {"name": "SQLServer2", "location": "East US", "databases": ["HRDB"]}]},
    {"resource_group": "ResourceGroup2", "servers": [
        {"name": "SQLServer3", "location": "Central India", "databases": ["FinanceDB"]},
        {"name": "SQLServer4", "location": "West US 2", "databases": ["AuditDB"]}]},
]
BASE = {
    "azure_topology": {"source": "inventory", "inventory": INVENTORY},
    "source": {"type": "mssql", "driver": "ODBC Driver 18 for SQL Server"},
    "azure": {"storage_account": "stgdefault", "container": "c",
              "storage_accounts_by_location": {"Central India": "stgindia"}},
    "gcp": {},
}


def cfg(**gcp):
    s = copy.deepcopy(BASE)
    s["gcp"].update(gcp)
    return s


# ---- the brief's target architecture, end to end ------------------------------------------
def test_brief_architecture_maps_exactly():
    targets = build_migration_targets(cfg())
    got = {(t.source.resource_group, t.source.server, t.source.database):
           (t.gcp_project_id, t.dataset_id, t.gcp_location) for t in targets}
    assert got == {
        ("ResourceGroup1", "SQLServer1", "SalesDB"): ("resourcegroup1", "sqlserver1_salesdb", "asia-south1"),
        ("ResourceGroup1", "SQLServer2", "HRDB"): ("resourcegroup1", "sqlserver2_hrdb", "us-east4"),
        ("ResourceGroup2", "SQLServer3", "FinanceDB"): ("resourcegroup2", "sqlserver3_financedb", "asia-south1"),
        ("ResourceGroup2", "SQLServer4", "AuditDB"): ("resourcegroup2", "sqlserver4_auditdb", "us-west1"),
    }
    t = targets[0]
    assert t.landing_bucket == "resourcegroup1-landing-asia-south1"
    assert t.control_dataset_id == "migration_control_asia_south1"
    assert t.object_prefix == "resourcegroup1/sqlserver1/salesdb"
    assert t.source.server_fqdn == "SQLServer1.database.windows.net"
    assert "Azure RG ResourceGroup1  ->  GCP project resourcegroup1" in format_topology(targets)


def test_us_multi_region_strategy():
    targets = build_migration_targets(cfg(location_mapping={"strategy": "multi_region"}))
    locs = {t.source.database: t.gcp_location for t in targets}
    assert locs == {"SalesDB": "asia-south1", "HRDB": "US", "FinanceDB": "asia-south1", "AuditDB": "US"}
    assert {t.landing_bucket for t in targets if t.gcp_location == "US"} == {
        "resourcegroup1-landing-us", "resourcegroup2-landing-us"}


# ---- location mapping -----------------------------------------------------------------------
@pytest.mark.parametrize("azure,gcp", [
    ("centralindia", "asia-south1"), ("Central India", "asia-south1"), ("central-india", "asia-south1"),
    ("southindia", "asia-south1"), ("eastus", "us-east4"), ("East US 2", "us-east4"),
    ("centralus", "us-central1"), ("southcentralus", "us-south1"), ("westus", "us-west2"),
    ("westus2", "us-west1"), ("westus3", "us-west4"), ("westeurope", "europe-west4"),
])
def test_closest_mapping(azure, gcp):
    assert resolve_gcp_location(azure, {}) == gcp


def test_location_overrides_and_errors():
    s = {"gcp": {"location_mapping": {"overrides": {"Central India": "asia-south2", "East US": "US"}}}}
    assert resolve_gcp_location("centralindia", s) == "asia-south2"
    assert resolve_gcp_location("eastus", s) == "US"
    with pytest.raises(LocationMappingError, match="No GCP location"):
        resolve_gcp_location("marsnorth", {})
    with pytest.raises(LocationMappingError, match="not a valid"):
        resolve_gcp_location("eastus", {"gcp": {"location_mapping": {"overrides": {"eastus": "Mars 1"}}}})
    with pytest.raises(LocationMappingError, match="strategy"):
        resolve_gcp_location("eastus", {"gcp": {"location_mapping": {"strategy": "nearest"}}})


# ---- naming ------------------------------------------------------------------------------------
def test_project_ids():
    assert naming.project_id_for_resource_group("ResourceGroup1") == "resourcegroup1"
    assert naming.project_id_for_resource_group("RG_Sales.Prod (India)") == "rg-sales-prod-india"
    assert naming.project_id_for_resource_group("rg1") == "rg1-gcp"                 # min 6 chars
    assert naming.project_id_for_resource_group("1-finance") == "rg-1-finance"      # must start with a letter
    assert naming.project_id_for_resource_group("ResourceGroup1", {"projects": {"id_prefix": "Acme"}}) \
        == "acme-resourcegroup1"
    assert naming.project_id_for_resource_group("ResourceGroup1", {"projects": {
        "id_overrides": {"resourcegroup1": "acme-sales-prod"}}}) == "acme-sales-prod"
    long_a = naming.project_id_for_resource_group("rg-enterprise-data-platform-production-india-a")
    long_b = naming.project_id_for_resource_group("rg-enterprise-data-platform-production-india-b")
    assert len(long_a) <= 30 and len(long_b) <= 30 and long_a != long_b
    with pytest.raises(naming.NamingError):
        naming.project_id_for_resource_group("x", {"projects": {"id_overrides": {"x": "Bad_ID"}}})
    with pytest.raises(naming.NamingError):
        naming.project_id_for_resource_group("___")


def test_dataset_ids():
    assert naming.dataset_id_for("SQLServer1", "SalesDB") == "sqlserver1_salesdb"
    assert naming.dataset_id_for("sqlserver1.database.windows.net", "Sales-DB 2024") == "sqlserver1_sales_db_2024"
    assert naming.dataset_id_for("srv", "db", {"datasets": {"name_template": "{resource_group}_{server}_{database}"}},
                                 resource_group="RG1") == "rg1_srv_db"
    assert naming.dataset_id_for("SQLServer1", "SalesDB", {"datasets": {"overrides": {"sqlserver1/salesdb": "sales_in"}}}) \
        == "sales_in"
    assert naming.dataset_id_for("9srv", "db") == "ds_9srv_db"
    with pytest.raises(naming.NamingError, match="unknown placeholder"):
        naming.dataset_id_for("s", "d", {"datasets": {"name_template": "{region}_{database}"}})


def test_table_names():
    assert naming.bq_table_name("dbo", "Orders") == "Orders"
    assert naming.bq_table_name("sales", "Orders") == "sales__Orders"
    assert naming.bq_table_name("dbo", "dataset-06-10") == "dataset-06-10"
    assert naming.bq_table_name("dbo", "Order Lines") == "Order_Lines"
    assert naming.bq_table_name("dbo", "Orders", {"table_naming": {"schema_prefix": "always"}}) == "dbo__Orders"
    assert naming.bq_table_name("sales", "Orders", {"table_naming": {"schema_prefix": "never"}}) == "Orders"


def test_bucket_names_and_secret_suffixes():
    assert naming.landing_bucket_name("resourcegroup1", "asia-south1") == "resourcegroup1-landing-asia-south1"
    long = naming.landing_bucket_name("p" * 30, "northamerica-northeast2", {"landing_bucket": {
        "name_template": "{project_id}-migration-landing-{location}"}})
    assert len(long) <= 63
    assert naming.secret_suffixes("sqlserver1.database.windows.net", "centralindia", "ResourceGroup1") == \
        ["SQLSERVER1", "RESOURCEGROUP1", "CENTRALINDIA"]


# ---- discovery -------------------------------------------------------------------------------
def test_inventory_filters():
    s = cfg()
    d = AzureTopologyDiscovery(s)
    assert [x.database for x in d.discover({"resource_groups": ["resourcegroup2"]})] == ["FinanceDB", "AuditDB"]
    assert [x.database for x in d.discover({"azure_locations": ["Central India"]})] == ["SalesDB", "FinanceDB"]
    assert [x.database for x in d.discover({"servers": ["sqlserver*"], "databases": ["HRDB"]})] == ["HRDB"]
    s["azure_topology"]["exclude_databases"] = ["auditdb"]
    assert "AuditDB" not in [x.database for x in AzureTopologyDiscovery(s).discover()]


def test_inventory_validation():
    s = cfg(); s["azure_topology"]["inventory"] = [{"resource_group": "rg", "servers": [{"name": "s", "databases": ["d"]}]}]
    with pytest.raises(TopologyError, match="no location"):
        AzureTopologyDiscovery(s).discover()
    s["azure_topology"] = {"source": "magic"}
    with pytest.raises(TopologyError, match="not valid"):
        AzureTopologyDiscovery(s).discover()


def _fake_arm(servers):
    class Client:
        def __init__(self):
            self.servers = SimpleNamespace(list=lambda: [s for s, _ in servers])
            self.databases = SimpleNamespace(list_by_server=lambda rg, name: dict(
                ((s.name, rg_), dbs) for s, dbs in servers for rg_ in [rg])[(name, rg)])
    return lambda sub: Client()


def _server(sub, rg, name, loc):
    return SimpleNamespace(id=f"/subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.Sql/servers/{name}",
                           name=name, location=loc, fully_qualified_domain_name=f"{name}.database.windows.net")


def _db(name, status="Online", loc=None):
    return SimpleNamespace(name=name, status=status, location=loc)


def test_arm_discovery():
    servers = [
        (_server("sub1", "ResourceGroup1", "sqlserver1", "centralindia"), [_db("master"), _db("SalesDB")]),
        (_server("sub1", "ResourceGroup1", "sqlserver2", "eastus"), [_db("HRDB"), _db("Old", "Offline")]),
        (_server("sub1", "ResourceGroup2", "sqlserver3", "centralindia"), [_db("FinanceDB", "Paused")]),
    ]
    s = cfg(); s["azure_topology"] = {"source": "arm", "subscription_ids": ["sub1"]}
    found = AzureTopologyDiscovery(s, sql_client_factory=_fake_arm(servers)).discover()
    assert [(d.resource_group, d.server, d.database, d.azure_location) for d in found] == [
        ("ResourceGroup1", "sqlserver1", "SalesDB", "centralindia"),
        ("ResourceGroup1", "sqlserver2", "HRDB", "eastus"),
        ("ResourceGroup2", "sqlserver3", "FinanceDB", "centralindia"),    # paused serverless is kept
    ]
    assert found[0].subscription_id == "sub1"


def test_arm_needs_subscription(monkeypatch):
    monkeypatch.delenv("AZURE_SUBSCRIPTION_ID", raising=False)
    s = cfg(); s["azure_topology"] = {"source": "arm"}
    with pytest.raises(TopologyError, match="subscription"):
        AzureTopologyDiscovery(s, sql_client_factory=lambda sub: None).discover()


# ---- validation ------------------------------------------------------------------------------
def _db_rec(rg, server, db, loc="centralindia", sub=None):
    return AzureSqlDatabase(rg, server, f"{server}.database.windows.net", db, loc, sub)


def test_same_rg_name_in_two_subscriptions_is_rejected():
    dbs = [_db_rec("rg-sales", "s1", "a", sub="sub1"), _db_rec("rg-sales", "s2", "b", sub="sub2")]
    with pytest.raises(TopologyError, match="both map to project"):
        build_migration_targets(cfg(), databases=dbs)


def test_dataset_clash_is_rejected():
    dbs = [_db_rec("rg1", "srv", "sales-db"), _db_rec("rg1", "srv", "sales_db")]
    with pytest.raises(TopologyError, match="both map to dataset"):
        build_migration_targets(cfg(), databases=dbs)


def test_unmappable_location_is_reported():
    with pytest.raises(TopologyError, match="No GCP location"):
        build_migration_targets(cfg(), databases=[_db_rec("rg1", "srv", "db", loc="atlantis")])


# ---- per-target settings ----------------------------------------------------------------------
def test_settings_for_target():
    s = cfg()
    before = copy.deepcopy(s)
    t = build_migration_targets(s)[0]
    r = settings_for_target(s, t)
    assert s == before                                       # input untouched
    assert r["source"]["host"] == "SQLServer1.database.windows.net"
    assert r["source"]["database"] == "SalesDB" and r["source"]["resource_group"] == "ResourceGroup1"
    assert r["gcp"]["project_id"] == "resourcegroup1" and r["gcp"]["dataset"] == "sqlserver1_salesdb"
    assert r["gcp"]["location"] == "asia-south1" and r["gcp"]["gcs_bucket"] == "resourcegroup1-landing-asia-south1"
    assert r["azure"]["storage_account"] == "stgindia"       # per-location staging account
    assert settings_for_target(r, t) is r                    # resolving twice is a no-op
    other = build_migration_targets(s)[1]
    with pytest.raises(TopologyError, match="already resolved"):
        settings_for_target(r, other)
    assert MigrationTarget.from_dict(t.to_dict()) == t        # XCom round trip
    assert settings_for_target(s, other)["azure"]["storage_account"] == "stgdefault"


def test_paths_carry_rg_server_db_scope_and_unique_run():
    r = settings_for_target(cfg(), build_migration_targets(cfg())[0])
    tag = run_tag()
    assert tag.startswith("run_") and tag != run_tag()                     # unique per run
    rel = run_rel_dir(r, "dbo", "Orders", "partial:abc123", tag)
    assert rel == f"parquet/resourcegroup1/sqlserver1/salesdb/dbo/Orders/partial-abc123/{tag}/"
    assert run_rel_dir(r, "dbo", "Order Details", "full", "c").startswith(
        "parquet/resourcegroup1/sqlserver1/salesdb/dbo/Order_Details-")   # safe + hashed
    assert local_dir(r, rel).replace("\\", "/") == "data/" + rel.rstrip("/")
    assert batch_filename(7) == "part_00007.parquet"


def test_unmappable_database_is_skipped_not_fatal():
    problems = []
    dbs = [_db_rec("rg1", "srv", "good"), _db_rec("rg1", "srv", "bad", loc="atlantis")]
    targets = build_migration_targets(cfg(), databases=dbs, problems_out=problems)
    assert [t.source.database for t in targets] == ["good"] and "atlantis" in problems[0]
    with pytest.raises(TopologyError):
        build_migration_targets(cfg(), databases=dbs, strict=True)


def test_arm_skips_replicas_and_dedicated_pools():
    servers = [SimpleNamespace(id="/subscriptions/s/resourceGroups/RG1/providers/x/servers/srv", name="srv",
                               location="centralindia", fully_qualified_domain_name="srv.database.windows.net")]
    dbs = [SimpleNamespace(name="db1", status="Online", location="centralindia", kind="v12.0,user"),
           SimpleNamespace(name="db1-geo", status="Online", location="centralindia", kind="v12.0,user",
                           secondary_type="Geo"),
           SimpleNamespace(name="pool", status="Online", location="centralindia", kind="v12.0,user,datawarehouse"),
           SimpleNamespace(name="master", status="Online", location="centralindia", kind="v12.0,system")]
    client = SimpleNamespace(servers=SimpleNamespace(list=lambda: servers),
                             databases=SimpleNamespace(list_by_server=lambda rg, s: dbs))
    s = cfg()
    s["azure_topology"] = {"source": "arm", "subscription_ids": ["s"]}
    found = AzureTopologyDiscovery(s, sql_client_factory=lambda sub: client).discover()
    assert [d.database for d in found] == ["db1"]
    s["azure_topology"]["include_secondaries"] = True
    found = AzureTopologyDiscovery(s, sql_client_factory=lambda sub: client).discover()
    assert [d.database for d in found] == ["db1", "db1-geo"]


def test_column_names_sanitized_and_unique():
    assert naming.bq_column_names(["Order Date", "order_date", "1st", "_TABLE_x", "ok"]) == \
        ["Order_Date", "order_date_2", "_1st", "c_TABLE_x", "ok"]
    assert naming.bq_column_names(["A b", "a B"], "keep") == ["A b", "a B_2"]
