"""Offline tests for the table inventory (no SQL Server / BigQuery needed)."""
import copy
import json
import pytest

from src.inventory import table_inventory as ti  # noqa: E402

BASE = {
    "source": {"type": "mssql", "host": "legacy.database.windows.net", "database": "db"},
    "azure": {"storage_account": "a", "container": "c"},
    "gcp": {"project_id": "p", "gcs_bucket": "bkt", "bq_dataset": "ds"},
    "regions": {
        "East US": {"host": "e.database.windows.net", "gcp_location": "us-east4"},
        "Germany West Central": {"host": "g.database.windows.net", "gcp_location": "europe-west3"},
        "Brazil South": {"host": "<todo>.database.windows.net", "gcp_location": "southamerica-east1"},
    },
}

SERVERS = {   # host -> {schema: [tables]}
    "e.database.windows.net": {"dbo": ["orders", "Shared"]},
    "g.database.windows.net": {"dbo": ["Customer"], "sales": ["shared"]},
}


class FakeDiscovery:
    def __init__(self, resolved):
        self.host = resolved["source"]["host"]
        self.data = SERVERS[self.host]
        self._engine = None

    def list_schemas(self):
        return sorted(self.data)

    def list_tables(self, schema):
        return list(self.data[schema])


class Broken(FakeDiscovery):
    def list_schemas(self):
        raise RuntimeError("timeout reaching server")


@pytest.fixture
def settings(tmp_path):
    s = copy.deepcopy(BASE)
    s["inventory"] = {"file": str(tmp_path / "inv.json"), "bigquery_sync": False}
    return s


def test_refresh_builds_inventory_and_skips_placeholder_hosts(settings):
    inv = ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    assert set(inv["regions"]) == {"East US", "Germany West Central"}
    assert json.load(open(settings["inventory"]["file"]))["regions"]["East US"]["tables"][0]["name"] == "orders"


def test_find_is_case_insensitive_and_returns_canonical_name(settings):
    inv = ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    hits = ti.find_table(inv, "CUSTOMER")
    assert [(h["region"], h["schema"], h["name"]) for h in hits] == [("Germany West Central", "dbo", "Customer")]


def test_locate_regions_single_hit_sets_schema(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    found = ti.locate_regions(settings, "orders")
    assert found == {"East US": {"name": "orders", "schema": "dbo"}}


def test_same_table_on_two_servers(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    found = ti.locate_regions(settings, "shared")
    assert set(found) == {"East US", "Germany West Central"}
    assert found["Germany West Central"]["schema"] == "sales"


def test_schema_qualified_lookup(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    assert list(ti.locate_regions(settings, "shared", "sales")) == ["Germany West Central"]


def test_miss_triggers_one_automatic_refresh(settings):
    calls = []

    def fake_refresh(s):
        calls.append(1)
        return ti.refresh_inventory(s, discovery_factory=FakeDiscovery)

    found = ti.locate_regions(settings, "customer", refresh=fake_refresh)     # no inventory file yet
    assert list(found) == ["Germany West Central"] and len(calls) == 1


def test_hit_does_not_refresh(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    ti.locate_regions(settings, "orders", refresh=lambda s: pytest.fail("must not rescan"))


def test_not_found_error_names_unscanned_servers(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    inv = ti.load_inventory(settings["inventory"]["file"])
    inv["regions"]["East US"]["status"] = "ERROR"
    ti.save_inventory(settings["inventory"]["file"], inv)
    with pytest.raises(ti.InventoryError, match="East US"):
        ti.locate_regions(settings, "nope", auto_refresh=False)


def test_failed_server_keeps_previous_tables(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    inv = ti.refresh_inventory(settings, "East US", discovery_factory=Broken)
    east = inv["regions"]["East US"]
    assert east["status"] == "ERROR" and "timeout" in east["error"]
    assert [t["name"] for t in east["tables"]] == ["orders", "Shared"]


def test_partial_refresh_keeps_other_regions(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    inv = ti.refresh_inventory(settings, "East US", discovery_factory=FakeDiscovery)
    assert "Germany West Central" in inv["regions"]


def test_full_refresh_drops_removed_regions(settings):
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    del settings["regions"]["Germany West Central"]
    inv = ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    assert set(inv["regions"]) == {"East US"}


def test_corrupt_file_gives_clear_error(settings):
    open(settings["inventory"]["file"], "w").write("{not json")
    with pytest.raises(ti.InventoryError, match="refresh-inventory"):
        ti.load_inventory(settings["inventory"]["file"])


def test_bigquery_sync_never_raises():
    bad = {"gcp": {}}                       # KeyError inside
    assert ti.sync_to_bigquery(bad, ti.empty_inventory()) is False


def test_inventory_rows_flatten():
    inv = {"regions": {"East US": {"host": "h", "database": "d", "scanned_at": "t", "status": "OK",
                                    "tables": [{"schema": "dbo", "name": "x"}]}}}
    assert ti.inventory_rows(inv)[0]["table_name"] == "x"
