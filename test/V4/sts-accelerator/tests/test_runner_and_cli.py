"""Planning across databases, parallel execution limits, locks and the CLI end to end (dry run)."""
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

import main
import src.orchestration.runner as runner
from src.metadata.lock_manager import LockManager, TableLockedError
from src.selection.run_spec import Job
from src.topology.targets import build_migration_targets
from tests.helpers import FakeReader, base_settings, resolved


def plan(settings, **job):
    job.setdefault("mode", "full")
    targets = build_migration_targets(settings)
    return runner.plan_job(settings, targets, Job(**job).validate(), reader_factory=FakeReader)


def labels(result):
    return sorted(runner.item_label(i) for i in result.items)


def test_one_table_everywhere_vs_one_database():
    s = base_settings()
    assert labels(plan(s, tables=["dbo.Orders"])) == [
        "ResourceGroup1/SQLServer1/SalesDB:dbo.Orders", "ResourceGroup1/SQLServer2/HRDB:dbo.Orders",
        "ResourceGroup2/SQLServer3/FinanceDB:dbo.Orders"]
    assert labels(plan(s, tables=["dbo.Orders"], databases=["SalesDB"])) == [
        "ResourceGroup1/SQLServer1/SalesDB:dbo.Orders"]


def test_list_of_tables_across_specific_databases_and_not_found():
    r = plan(base_settings(), tables=["SalesDB/dbo.Orders", "HRDB/dbo.Customers", "FinanceDB/dbo.Nope"])
    assert labels(r) == ["ResourceGroup1/SQLServer1/SalesDB:dbo.Orders", "ResourceGroup1/SQLServer2/HRDB:dbo.Customers"]
    assert r.unmatched == ["FinanceDB/dbo.Nope"]


def test_whole_database_and_multiple_databases_and_rg():
    s = base_settings()
    assert len(plan(s, all_tables=True, databases=["SalesDB"]).items) == 4
    assert len(plan(s, all_tables=True, databases=["SalesDB", "HRDB"]).items) == 8
    assert len(plan(s, all_tables=True, resource_groups=["ResourceGroup2"]).items) == 4
    inc = plan(s, all_tables=True, mode="incremental", databases=["SalesDB"])
    assert len(inc.items) == 3 and [r for _, r in inc.skipped] == [
        "incremental needs a primary key, unique index or key_columns override"]   # only the heap


def test_slim_items_for_airflow_and_replan():
    s = base_settings()
    r = plan(s, tables=["dbo.Orders"], databases=["SalesDB"])
    slim = runner.plan_job(s, build_migration_targets(s), Job(mode="full", tables=["dbo.Orders"]).validate(),
                           slim=True, reader_factory=FakeReader)
    assert slim.items[0]["table"] is None and r.items[0]["table"]["name"] == "Orders"
    with mock.patch.object(runner, "plan_one", return_value=r.items[0]["table"]) as po, \
            mock.patch.object(runner, "run_table", return_value={"status": "SUCCESS"}) as rt:
        runner.run_work_item(s, {}, slim.items[0], "b")
    po.assert_called_once() and rt.assert_called_once()


def test_parallel_respects_per_server_limit():
    items = [{"target": {"source": {"server_fqdn": "a" if i < 4 else "b", "resource_group": "r", "server": "s",
                                    "database": "d"}}, "schema": "dbo", "name": f"t{i}"} for i in range(6)]
    live, peak, lock = {"a": 0, "b": 0}, {"a": 0, "b": 0}, threading.Lock()

    def fake_run(settings, lc, item, run_id, ignore_locks):
        srv = item["target"]["source"]["server_fqdn"]
        with lock:
            live[srv] += 1
            peak[srv] = max(peak[srv], live[srv])
        time.sleep(0.05)
        with lock:
            live[srv] -= 1
        if item["name"] == "t5":
            raise RuntimeError("boom")
        return {"table": item["name"], "status": "SUCCESS"}

    with mock.patch.object(runner, "run_work_item", side_effect=fake_run):
        results = runner.run_items({}, {}, items, "b", parallel=6, max_per_server=2)
    assert peak["a"] == 2 and len(results) == 6
    assert [r["status"] for r in results].count("FAILED") == 1           # one failure doesn't stop the rest


def test_lock_acquire_conflict_and_release():
    held = {}

    class Client:
        def query(self, sql, job_config=None):
            params = {p.name: p.value for p in job_config.query_parameters}
            if sql.strip().startswith("MERGE"):
                held.setdefault(params["k"], params["o"])
            elif sql.strip().startswith("DELETE") and held.get(params["k"]) == params.get("o"):
                held.pop(params["k"])
            owner = held.get(params["k"])
            return SimpleNamespace(result=lambda: [SimpleNamespace(owner=owner)] if owner else [])

    s = resolved(base_settings())
    a = LockManager(s, client=Client(), owner="A")
    b = LockManager(s, client=Client(), owner="B")
    with a.hold("t1"):
        with pytest.raises(TableLockedError, match="already being migrated by this process"):
            b.acquire("t1")
    with b.hold("t1"):
        assert held == {"t1": "B"}
    assert held == {}


@pytest.fixture
def cli_env(tmp_path):
    settings = base_settings(tmp_path)
    with mock.patch.object(main, "load_configs", return_value=(settings, {})), \
            mock.patch.object(runner, "CatalogReader", FakeReader), \
            mock.patch.object(main, "plan_job", side_effect=lambda *a, **k: runner.plan_job(
                *a, **{**k, "reader_factory": FakeReader})):
        yield settings


def test_cli_dry_run_list_and_errors(cli_env, capsys):
    assert main.main(["--tables", "dbo.Orders", "sales.Orders", "--database", "SalesDB", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "dbo.Orders" in out and "sales__Orders" in out and "HRDB" not in out.split("### job")[1]
    assert main.main(["--list", "--database", "HRDB", "--mode", "incremental"]) == 0
    out = capsys.readouterr().out
    assert "SKIP ResourceGroup1/SQLServer2/HRDB:dbo.EventLog" in out
    assert main.main(["--tables", "dbo.Nope", "--dry-run", "--strict"]) == 1
    with pytest.raises(SystemExit):
        main.main([])
    with pytest.raises(SystemExit):
        main.main(["--all", "--where", "x=1"])                            # needs --mode partial


def test_cli_migrate_calls_runner(cli_env):
    with mock.patch.object(main, "provision", return_value={}), \
            mock.patch.object(main, "run_items", return_value=[{"table": "x", "status": "SUCCESS", "rows": 1}]) as ri, \
            mock.patch.object(main, "generate_reports", return_value=[]):
        assert main.main(["--all", "--database", "SalesDB", "--parallel", "3", "--skip-provision"]) == 0
    assert ri.call_args.kwargs["parallel"] == 3 and len(ri.call_args.args[2]) == 4
