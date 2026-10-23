"""The generated T-SQL and BigQuery SQL: content checks plus a real parser
(sqlglot) so a syntax slip can't reach production."""
import datetime

import pytest
import sqlglot

from src.bigquery.bq_loader import build_delete_range_sql, build_insert_sql, build_merge_sql, build_replace_sql
from src.extraction.sql_extractor import (
    build_page_query,
    build_stream_query,
    filter_conditions,
    keyset_condition,
)
from src.planner.table_planner import plan_table
from src.selection.run_spec import Job
from tests.helpers import CUSTOMERS, HEAP, ORDERS, base_settings, col, entry, resolved


def tsql_ok(sql: str) -> None:
    # sqlglot doesn't know named binds; swap them for literals before parsing
    import re
    sqlglot.parse_one(re.sub(r":\w+", "'1'", sql), read="tsql")


def bq_ok(sql: str) -> None:
    sqlglot.parse_one(sql, read="bigquery")


def cfg_for(e, mode="full", **job):
    job.setdefault("tables", ["x"])
    return plan_table(e, resolved(base_settings()), Job(mode=mode, **job).validate())


def test_keyset_single_and_composite():
    k1 = [col("Id", "int") | {"decl": "int"}]
    sql, params = keyset_condition(k1, {"Id": 5})
    assert sql == "[Id] > CAST(:k_0 AS int)" and params == {"k_0": 5}
    k2 = [col("A", "varchar") | {"decl": "varchar(10)"}, col("B", "datetime") | {"decl": "datetime"}]
    sql, params = keyset_condition(k2, {"A": "x", "B": datetime.datetime(2024, 1, 1)})
    assert sql == ("[A] >= CAST(:k_0 AS varchar(10)) AND (([A] > CAST(:k_0 AS varchar(10))) OR "
                   "([A] = CAST(:k_0 AS varchar(10)) AND [B] > CAST(:k_1 AS datetime)))")
    assert keyset_condition(k1, None) == (None, {})


def test_full_page_query_with_cutoff_and_resume():
    s = base_settings()
    s["extraction"]["cutoff_date"] = "2026-09-22"
    cfg = plan_table(ORDERS, resolved(s), Job(mode="full", all_tables=True).validate())
    conds, params = filter_conditions(cfg)
    assert conds == ["[updatedAt] < CAST(:cutoff AS datetime2(7))"]
    assert params["cutoff"] == datetime.datetime(2026, 9, 23)              # through the end of the day
    sql, kp = build_page_query(cfg, ("dbo", "Orders"), 1000, conds, {"OrderId": 41})
    assert sql.startswith("SELECT TOP (1000) [OrderId] AS [OrderId], [Customer] AS [Customer]")
    assert "ORDER BY [OrderId]" in sql and "[OrderId] > CAST(:k_0 AS int)" in sql and kp == {"k_0": 41}
    tsql_ok(sql)


def test_incremental_conditions_value_and_rowversion():
    cfg = cfg_for(ORDERS, "incremental")
    assert cfg["watermark"]["column"] == "updatedAt"                     # first of incremental.watermark_columns
    lo, hi = datetime.datetime(2026, 1, 1), datetime.datetime(2026, 2, 1)
    conds, params = filter_conditions(cfg, lo, hi)
    assert conds == ["[updatedAt] > CAST(:wm_low AS datetime2(7))", "[updatedAt] <= CAST(:wm_high AS datetime2(7))"]
    conds, _ = filter_conditions(cfg, None, hi)                          # first run: NULL watermarks included
    assert conds == ["([updatedAt] <= CAST(:wm_high AS datetime2(7)) OR [updatedAt] IS NULL)"]

    rv = cfg_for(CUSTOMERS, "incremental")
    assert rv["watermark"] == {"column": "RowVer", "kind": "rowversion", "is_key": False, "bq_column": "RowVer"}
    conds, params = filter_conditions(rv, "0x00000000000007D0", "0x0000000000000800")
    assert conds == ["[RowVer] > CONVERT(binary(8), :wm_low, 1)", "[RowVer] < CONVERT(binary(8), :wm_high, 1)"]
    sql, _ = build_page_query(rv, ("dbo", "Customers"), 10, conds, {"CustomerId": "ABC"})
    assert "CAST(:k_0 AS uniqueidentifier)" in sql
    tsql_ok(sql)


def test_partial_conditions():
    e = entry("dbo", "T", [col("Id", "int"), col("Region", "varchar", max_length=5), col("TxnDate", "date")],
              pk=["Id"])
    cfg = cfg_for(e, "partial", where="Region = 'IN'", date_column="txndate", date_from="2026-01-01",
                  date_to="2026-02-01")
    conds, params = filter_conditions(cfg)
    assert conds == ["Region = 'IN'", "[TxnDate] >= CAST(:d_from AS date)", "[TxnDate] < CAST(:d_to AS date)"]
    assert params == {"d_from": datetime.date(2026, 1, 1), "d_to": datetime.date(2026, 2, 1)}
    tsql_ok(build_page_query(cfg, ("dbo", "T"), 5, conds, None)[0])


def test_stream_query_for_heap():
    cfg = cfg_for(HEAP, "full", all_tables=True, tables=[])
    assert cfg["strategy"] == "stream" and cfg["merge"] == "append"
    sql = build_stream_query(cfg, [])
    assert sql == "SELECT [Message] AS [Message], [At] AS [At] FROM [dbo].[EventLog]"
    tsql_ok(sql)


def test_merge_insert_only_upsert_append():
    t, s = "p.ds.Orders", "p.ctl.stg__ds__Orders"
    full = cfg_for(ORDERS, "full")
    sql = build_merge_sql(t, s, full)
    assert "WHEN MATCHED" not in sql and "QUALIFY ROW_NUMBER() OVER (PARTITION BY S0.`OrderId`) = 1" in sql
    assert "CAST(S0.`ModifiedDate` AS DATETIME) AS `ModifiedDate`" in sql
    bq_ok(sql)

    inc = cfg_for(ORDERS, "incremental")
    sql = build_merge_sql(t, s, inc)
    assert "ORDER BY S0.`updatedAt` DESC" in sql
    assert "WHEN MATCHED AND (T.`updatedAt` IS NULL OR S.`updatedAt` >= T.`updatedAt`) THEN UPDATE SET" in sql
    assert "T.`OrderId` = S.`OrderId`" in sql and "T.`OrderId` = S.`OrderId`," not in sql.split("UPDATE SET")[1]
    bq_ok(sql)

    heap = cfg_for(HEAP, "full")
    sql = build_merge_sql(t, s, heap)
    assert sql.startswith("INSERT INTO `p.ds.Orders` (`Message`, `At`)")
    bq_ok(sql)


def test_append_only_incremental_uses_insert_only():
    e = entry("dbo", "Log", [col("Id", "bigint", is_identity=True), col("Msg", "nvarchar")], pk=["Id"])
    cfg = cfg_for(e, "incremental")
    assert cfg["watermark"]["is_key"] and cfg["merge"] == "insert_only"
    assert "WHEN MATCHED" not in build_merge_sql("p.d.t", "p.c.s", cfg)


def test_full_load_replaces_rows_in_one_transaction():
    t, s = "p.ds.Orders", "p.ctl.stg__ds__Orders"
    sql = build_replace_sql(t, s, cfg_for(ORDERS, "full"))
    assert sql.startswith("BEGIN TRANSACTION;\nDELETE FROM `p.ds.Orders` WHERE TRUE;\nINSERT INTO `p.ds.Orders`")
    assert sql.endswith("COMMIT TRANSACTION;")
    assert "QUALIFY ROW_NUMBER() OVER (PARTITION BY S0.`OrderId`) = 1" in sql
    for stmt in sql.split(";\n")[1:3]:
        bq_ok(stmt)
    heap = build_insert_sql(t, s, cfg_for(HEAP, "full"))
    assert "QUALIFY" not in heap
    bq_ok(heap)


def test_delete_range_sql():
    sql = build_delete_range_sql("p.d.t", "TxnDate", "DATE", True, True)
    assert sql == "DELETE FROM `p.d.t` WHERE `TxnDate` >= CAST(@d_from AS DATE) AND `TxnDate` < CAST(@d_to AS DATE)"
    bq_ok(sql)


@pytest.mark.parametrize("name", ["weird`name", "Order Date"])
def test_identifiers_are_escaped(name):
    e = entry("dbo", "T", [col("Id", "int"), col(name, "int")], pk=["Id"])
    s = base_settings()
    s["gcp"]["column_naming"] = "keep"
    cfg = plan_table(e, resolved(s), Job(mode="full", tables=["x"]).validate())
    assert "`" not in cfg["columns"][1]["bq_name"]
    bq_ok(build_merge_sql("p.d.t", "p.c.s", cfg))
