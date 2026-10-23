import sys, pathlib, copy
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.config import region_resolver as rr
from src.gcp import project_manager as pm


def cfg(enabled=True, **mp):
    base = {
        "source": {"database": "db"},
        "azure": {"container": "c"},
        "gcp": {"project_id": "conn-proj", "gcs_bucket": "land", "bq_dataset": "migrated_data"},
        "multi_project": {"enabled": enabled, "billing_account": "AAA-BBB", "folder_id": "123",
                          "runner_service_account": "sa@x.iam.gserviceaccount.com", **mp},
        "regions": {
            "rg1-s1": {"host": "sqlserver1.database.windows.net", "database": "SalesDB",
                       "gcp_location": "us-east4", "resource_group": "ResourceGroup1", "server": "SQLServer1"},
            "rg1-s2": {"host": "sqlserver2.database.windows.net", "database": "HRDB",
                       "gcp_location": "us-east4", "resource_group": "ResourceGroup1", "server": "SQLServer2"},
            "rg2-s1": {"host": "other.database.windows.net", "database": "SalesDB",
                       "gcp_location": "europe-west3", "resource_group": "ResourceGroup2"},
        },
    }
    return base


def test_project_id_and_prefix():
    c = cfg()
    assert rr.project_id_for(c, "ResourceGroup1") == "mig-resourcegroup1"
    assert rr.project_id_for(c, "rg_na East") == "mig-rg-na-east"


def test_long_rg_is_shortened_and_unique():
    c = cfg()
    a = rr.project_id_for(c, "a-very-long-resource-group-name-number-one")
    b = rr.project_id_for(c, "a-very-long-resource-group-name-number-two")
    assert len(a) <= 30 and len(b) <= 30 and a != b


def test_explicit_project_override():
    c = cfg(projects={"resourcegroup1": "my-company-rg1"})
    assert rr.project_id_for(c, "ResourceGroup1") == "my-company-rg1"


def test_bad_explicit_id_rejected():
    with pytest.raises(rr.RegionError):
        rr.project_id_for(cfg(projects={"ResourceGroup1": "Bad_ID"}), "ResourceGroup1")


def test_resolve_multi_sets_project_bucket_datasets():
    r = rr.resolve_region(cfg(), "rg1-s1")
    assert r["gcp"]["project_id"] == "mig-resourcegroup1"
    assert r["gcp"]["base_project_id"] == "conn-proj"
    assert r["gcp"]["gcs_bucket"] == "mig-resourcegroup1-rg1-s1"
    assert r["source"]["dataset_prefix"] == "sqlserver1_salesdb"
    from src.bigquery.dataset_naming import dataset_for_schema
    assert dataset_for_schema("x", "dbo", r["source"]["dataset_prefix"]) == "sqlserver1_salesdb_dbo"


def test_same_table_name_two_servers_never_share_dataset():
    a = rr.resolve_region(cfg(), "rg1-s1")["source"]["dataset_prefix"]
    b = rr.resolve_region(cfg(), "rg1-s2")["source"]["dataset_prefix"]
    assert a != b


def test_disabled_is_unchanged():
    r = rr.resolve_region(cfg(enabled=False), "rg1-s1")
    assert r["gcp"]["project_id"] == "conn-proj"
    assert "dataset_prefix" not in r["source"]
    assert r["gcp"]["gcs_bucket"] == "land-rg1-s1"


def test_enabled_needs_resource_group():
    c = cfg(); del c["regions"]["rg1-s1"]["resource_group"]
    with pytest.raises(rr.RegionError):
        rr.resolve_region(c, "rg1-s1")


def test_select_targets():
    c = cfg()
    assert rr.select_targets(c, None, "ResourceGroup1", None) == ["rg1-s1", "rg1-s2"]
    assert rr.select_targets(c, None, "resourcegroup1", "sqlserver2") == ["rg1-s2"]
    assert rr.select_targets(c, "all", None, None) == ["rg1-s1", "rg1-s2", "rg2-s1"]
    assert rr.select_targets(c, None, "all", None) == ["rg1-s1", "rg1-s2", "rg2-s1"]
    assert rr.select_targets(c, "rg2-s1", None, None) == ["rg2-s1"]
    with pytest.raises(rr.RegionError):
        rr.select_targets(c, None, "nope", None)
    with pytest.raises(rr.RegionError):
        rr.select_targets(c, None, None, None)          # still must say what to run


# ---- project_manager ----------------------------------------------------------------------
class FakeGcloud:
    def __init__(self, exists=False, billing="", fail=None):
        self.calls, self.exists, self.billing, self.fail = [], exists, billing, fail or {}

    def __call__(self, args):
        self.calls.append(args)
        key = " ".join(args[:2])
        if key in self.fail:
            return 1, "", self.fail[key]
        if key == "projects describe":
            return (0, "ACTIVE", "") if self.exists else (1, "", "PERMISSION_DENIED or not found")
        if key == "billing projects" and args[2] == "describe":
            return 0, self.billing, ""
        return 0, "", ""


def resolved(**mp):
    return rr.resolve_region(cfg(**mp), "rg1-s1")


@pytest.fixture(autouse=True)
def _clear():
    pm._ENSURED.clear()


def test_creates_in_folder_and_links_billing():
    g = FakeGcloud()
    waited = []
    pid = pm.ensure_project(resolved(), run=g, wait_access=waited.append)
    flat = [" ".join(c) for c in g.calls]
    assert pid == "mig-resourcegroup1"
    assert any(c.startswith("projects create mig-resourcegroup1") and "--folder=123" in c for c in flat)
    assert any(c.startswith("billing projects link mig-resourcegroup1 --billing-account=AAA-BBB") for c in flat)
    assert any(c.startswith("services enable bigquery.googleapis.com storage.googleapis.com") for c in flat)
    assert not any("add-iam-policy-binding" in c for c in flat)      # folder inheritance: no per-project IAM
    assert waited == ["mig-resourcegroup1"]


def test_existing_project_with_billing_is_left_alone():
    g = FakeGcloud(exists=True, billing="True")
    pm.ensure_project(resolved(), run=g, wait_access=lambda p: None)
    flat = [" ".join(c) for c in g.calls]
    assert not any(c.startswith("projects create") for c in flat)
    assert not any("billing projects link" in c for c in flat)


def test_no_folder_grants_roles_per_project():
    g = FakeGcloud()
    pm.ensure_project(resolved(folder_id=""), run=g, wait_access=lambda p: None)
    flat = [" ".join(c) for c in g.calls]
    assert sum("add-iam-policy-binding" in c for c in flat) == 3
    assert not any("--folder=" in c for c in flat)


def test_idempotent_per_process():
    g = FakeGcloud()
    s = resolved()
    pm.ensure_project(s, run=g, wait_access=lambda p: None)
    n = len(g.calls)
    pm.ensure_project(s, run=g, wait_access=lambda p: None)
    assert len(g.calls) == n


def test_disabled_does_nothing():
    g = FakeGcloud()
    s = rr.resolve_region(cfg(enabled=False), "rg1-s1")
    assert pm.ensure_project(s, run=g) == "conn-proj" and g.calls == []


def test_missing_billing_is_clear_error():
    with pytest.raises(pm.ProjectError, match="billing_account"):
        pm.ensure_project(resolved(billing_account=""), run=FakeGcloud(), wait_access=lambda p: None)


def test_create_denied_message():
    g = FakeGcloud(fail={"projects create": "PERMISSION_DENIED: nope"})
    with pytest.raises(pm.ProjectError, match="projectCreator"):
        pm.ensure_project(resolved(), run=g, wait_access=lambda p: None)


def test_id_taken_message():
    g = FakeGcloud(fail={"projects create": "ALREADY_EXISTS: id in use"})
    with pytest.raises(pm.ProjectError, match="already taken"):
        pm.ensure_project(resolved(), run=g, wait_access=lambda p: None)
