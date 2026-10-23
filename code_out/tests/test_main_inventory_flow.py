"""main.py --table with/without --region, driven by the inventory (everything cloud is stubbed)."""
import copy
import sys
from unittest import mock

import pytest

STUBS = ("google", "google.api_core", "google.api_core.exceptions", "google.cloud",
         "google.cloud.bigquery", "google.cloud.storage", "google.cloud.storage_transfer_v1",
         "azure", "azure.storage", "azure.storage.blob", "dotenv", "sqlalchemy", "sqlalchemy.engine",
         "pandas", "pyarrow", "pyarrow.parquet", "pyodbc")

from src.inventory import table_inventory as ti  # noqa: E402
from tests.test_inventory import BASE, FakeDiscovery  # noqa: E402


@pytest.fixture
def run(tmp_path, monkeypatch):
    # stubs + freshly imported modules live only for this test (sys.modules is restored afterwards)
    patcher = mock.patch.dict(sys.modules, {n: mock.MagicMock() for n in STUBS})
    patcher.start()
    for m in [m for m in sys.modules if m == "main" or m.startswith("src.")]:
        sys.modules.pop(m)
    import main as app
    settings = copy.deepcopy(BASE)
    settings["inventory"] = {"file": str(tmp_path / "inv.json"), "bigquery_sync": False}
    ti.refresh_inventory(settings, discovery_factory=FakeDiscovery)
    seen = []

    def fake_execute(args, resolved, logging_config):
        seen.append((resolved["source"]["region"], resolved["source"]["host"], args.table, args.schema))

    monkeypatch.setattr(app, "execute", fake_execute)
    monkeypatch.setattr(app, "load_yaml", lambda p: settings if p.endswith("settings.yaml") else {})

    def go(*argv):
        monkeypatch.setattr(sys, "argv", ["main.py", *argv])
        seen.clear()
        app.main()
        return list(seen)
    yield go
    patcher.stop()          # restores sys.modules exactly as it was before this test


def test_table_only_finds_the_server(run):
    assert run("--table", "customer") == [("Germany West Central", "g.database.windows.net", "Customer", "dbo")]


def test_explicit_region_skips_inventory(run):
    assert run("--region", "East US", "--table", "whatever") == [("East US", "e.database.windows.net", "whatever", None)]


def test_table_on_two_servers_needs_a_choice(run):
    with pytest.raises(SystemExit, match="more than one server"):
        run("--table", "shared")


def test_region_all_runs_only_where_the_table_lives(run):
    got = run("--region", "all", "--table", "shared")
    assert sorted(g[0] for g in got) == ["East US", "Germany West Central"]


def test_schema_qualified_table(run):
    assert run("--table", "sales.shared") == [("Germany West Central", "g.database.windows.net", "shared", "sales")]


def test_unknown_table_error(run):
    with pytest.raises(SystemExit, match="not found on any configured"):
        run("--table", "nothing_like_it")
