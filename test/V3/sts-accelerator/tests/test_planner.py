"""Planner decisions per table and mode."""
import pytest
import yaml

from src.planner.table_planner import CutoffColumnError, TableNameClashError, plan_database, plan_table
from src.selection.run_spec import Job
from tests.helpers import CATALOG, CUSTOMERS, HEAP, ORDERS, SALES_ORDERS, base_settings, col, entry, resolved


def job(mode="auto", **kw):
    kw.setdefault("all_tables", True)
    return Job(mode=mode, **kw).validate()


def test_full_with_pk_unique_and_heap():
    s = resolved(base_settings())
    o = plan_table(ORDERS, s, job("full"))
    assert (o["strategy"], o["merge"], o["key"]["columns"], o["key"]["kind"]) == ("keyset", "insert_only",
                                                                                  ["OrderId"], "primary_key")
    u = plan_table(SALES_ORDERS, s, job("full"))
    assert u["key"] == {"columns": ["Id"], "kind": "unique_index", "bq_columns": ["Id"]}
    assert u["bq_table"] == "sales__Orders"
    h = plan_table(HEAP, s, job("full"))
    assert (h["strategy"], h["merge"], h["key"]["columns"]) == ("stream", "append", [])


def test_heap_shadow_and_exclude_strategies():
    s = base_settings()
    s["extraction"]["no_pk_strategy"] = "shadow_table"
    h = plan_table(HEAP, resolved(s), job("full"))
    assert h["strategy"] == "shadow" and h["key"]["columns"] == ["source_row_id"]
    assert h["columns"][-1]["name"] == "source_row_id"
    s["extraction"]["no_pk_strategy"] = "exclude"
    assert "no primary key" in plan_table(HEAP, resolved(s), job("full"))["skip_reason"]


def test_incremental_watermark_choices():
    s = resolved(base_settings())
    assert plan_table(ORDERS, s, job("incremental"))["watermark"]["column"] == "updatedAt"
    assert plan_table(CUSTOMERS, s, job("incremental"))["watermark"]["kind"] == "rowversion"
    ident = entry("dbo", "L", [col("Id", "bigint"), col("x", "int")], pk=["Id"])
    w = plan_table(ident, s, job("incremental"))["watermark"]
    assert w["is_key"] and w["column"] == "Id"
    nowm = entry("dbo", "N", [col("Code", "varchar", max_length=5), col("x", "int")], pk=["Code"])
    assert "no watermark" in plan_table(nowm, s, job("incremental"))["skip_reason"]
    assert "needs a primary key" in plan_table(HEAP, s, job("incremental"))["skip_reason"]
    s2 = base_settings()
    s2["incremental"] = {"no_watermark": "full"}
    assert plan_table(nowm, resolved(s2), job("incremental"))["mode"] == "full"


def test_overrides(tmp_path):
    (tmp_path / "overrides.yaml").write_text(yaml.safe_dump({"overrides": [
        {"name": "Orders", "schema": "dbo", "load_mode": "incremental", "watermark_column": "ModifiedDate"},
        {"name": "EventLog", "key_columns": ["At"]},
        {"name": "Customers", "database": "HRDB", "exclude": True},
        {"name": "Orders", "schema": "sales", "bq_table": "sales_orders"},
    ]}))
    s = base_settings(tmp_path)
    sales = {(t["schema"], t["name"]): t for t in plan_database(resolved(s, 0), job(), catalog=CATALOG)}
    assert sales[("dbo", "Orders")]["mode"] == "incremental"
    assert sales[("dbo", "Orders")]["watermark"]["column"] == "ModifiedDate"
    assert sales[("dbo", "EventLog")]["key"]["kind"] == "override"
    assert sales[("sales", "Orders")]["bq_table"] == "sales_orders"
    assert sales[("dbo", "Customers")]["skip_reason"] is None
    hr = {(t["schema"], t["name"]): t for t in plan_database(resolved(s, 1), job(), catalog=CATALOG)}
    assert hr[("dbo", "Customers")]["skip_reason"] == "excluded in tables_override.yaml"
    only_inc = plan_database(resolved(s, 0), job(only_modes=["incremental"]), catalog=CATALOG)
    assert [(t["schema"], t["name"]) for t in only_inc] == [("dbo", "Orders")]


def test_cutoff_policies():
    s = base_settings()
    s["extraction"].update(cutoff_date="2026-09-22", missing_cutoff_column="error")
    with pytest.raises(CutoffColumnError):
        plan_table(CUSTOMERS, resolved(s), job("full"))
    assert plan_table(CUSTOMERS, resolved(s), job("full", cutoff_date="none"))["filters"]["cutoff"] is None
    s["extraction"]["missing_cutoff_column"] = "skip_filter"
    assert plan_table(CUSTOMERS, resolved(s), job("full"))["filters"]["cutoff"] is None
    assert plan_table(ORDERS, resolved(s), job("full"))["filters"]["cutoff"] == {"column": "updatedAt",
                                                                                 "value": "2026-09-22"}
    assert plan_table(ORDERS, resolved(s), job("incremental"))["filters"]["cutoff"] is None   # never for incremental


def test_partial_needs_column_and_key_and_scopes_differ():
    s = resolved(base_settings())
    j1 = job("partial", date_column="updatedAt", date_from="2026-01-01")
    j2 = job("partial", date_column="updatedAt", date_from="2026-02-01")
    a, b = plan_table(ORDERS, s, j1), plan_table(ORDERS, s, j2)
    assert a["scope_key"].startswith("partial:") and a["scope_key"] != b["scope_key"]
    assert "no column" in plan_table(CUSTOMERS, s, j1)["skip_reason"]
    assert "needs a primary key" in plan_table(HEAP, s, job("partial", where="1=1"))["skip_reason"]


def test_clash_detected():
    s = base_settings()
    s["gcp"]["table_naming"] = {"schema_prefix": "never"}
    with pytest.raises(TableNameClashError):
        plan_database(resolved(s), job("full"), catalog=CATALOG)


def test_plan_sorted_largest_first_and_selected():
    s = resolved(base_settings())
    plans = plan_database(s, job("full", all_tables=False, tables=["dbo.*"], exclude_tables=["dbo.Customers"]),
                          catalog=CATALOG)
    assert [t["name"] for t in plans] == ["Orders", "EventLog"]
