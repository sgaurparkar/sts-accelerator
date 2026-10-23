"""Raw views / stored procedures: catalog reading, change detection, files, BigQuery
writes, planning, CLI — all offline (SQL Server, GCS and BigQuery are faked)."""
import copy
import json
import os
import re
from types import SimpleNamespace
from unittest import mock

import pytest
import sqlglot

import main
import src.orchestration.runner as runner
from src.objects import object_catalog as oc
from src.objects.object_pipeline import dependency_levels, object_rel_path, run_objects
from src.objects.object_store import ObjectStore, build_objects_merge_sql
from src.objects.object_types import ObjectTypeError, normalize_kinds, type_codes
from src.selection.run_spec import Job, RunSpecError, job_from_mapping
from src.topology.targets import build_migration_targets
from tests.helpers import FakeReader, base_settings, resolved

VIEW_SQL = "CREATE VIEW dbo.vw_Orders\r\nAS\r\n-- raw comment kept\r\nSELECT OrderId, Amount FROM dbo.Orders;\r\n"
PROC_SQL = ("CREATE PROCEDURE [dbo].[usp_Load] @From date, @Rows int OUTPUT AS\nBEGIN\n"
            "  SET NOCOUNT ON;\n  SELECT TOP 10 * FROM dbo.vw_Orders WHERE OrderDate >= @From;\nEND\n")
REPORT_SQL = "CREATE VIEW sales.vw_Report AS SELECT * FROM dbo.vw_Orders o JOIN OtherDB.dbo.X x ON 1=1"


def idx(oid, schema, name, kind, encrypted=False, code=None):
    return {"object_id": oid, "schema": schema, "name": name, "kind": kind,
            "type_code": code or {"view": "V", "procedure": "P", "function": "FN", "trigger": "TR"}[kind],
            "type_desc": kind.upper(), "created_at": "2026-01-01T00:00:00", "modified_at": "2026-02-01T00:00:00",
            "is_encrypted": encrypted, "parent_object": None}


def detail(o, definition, status="OK", refs=(), params=()):
    return {**o, "definition": definition, "definition_status": status, "uses_ansi_nulls": True,
            "uses_quoted_identifier": True, "is_schema_bound": False, "parameters": list(params), "returns": None,
            "columns": [], "references": list(refs)}


INDEX = [idx(1, "dbo", "vw_Orders", "view"), idx(2, "dbo", "usp_Load", "procedure"),
         idx(3, "sales", "vw_Report", "view"), idx(4, "dbo", "usp_Secret", "procedure", encrypted=True)]
DETAILS = {
    1: detail(INDEX[0], VIEW_SQL, refs=[{"schema": "dbo", "name": "Orders", "kind": "table"}]),
    2: detail(INDEX[1], PROC_SQL, refs=[{"schema": "dbo", "name": "vw_Orders", "kind": "view"}],
              params=[{"name": "@From", "data_type": "date"}, {"name": "@Rows", "data_type": "int"}]),
    3: detail(INDEX[2], REPORT_SQL, refs=[{"schema": "dbo", "name": "vw_Orders", "kind": "view"},
                                         {"database": "OtherDB", "schema": "dbo", "name": "X"}]),
    4: detail(INDEX[3], None, status="ENCRYPTED"),
}


class FakeObjectReader:
    def __init__(self, settings=None, index=None, details=None):
        self.index = copy.deepcopy(index if index is not None else INDEX)
        self.details = copy.deepcopy(details if details is not None else DETAILS)

    def read_index(self, kinds, schemas=None):
        return [o for o in self.index if o["kind"] in kinds and (not schemas or o["schema"] in schemas)]

    def read_details(self, objects):
        return [copy.deepcopy(self.details[o["object_id"]]) for o in objects]


class FakeStore:
    objects_ref = "resourcegroup1.migration_control_asia_south1.migration_sql_objects"

    def __init__(self, existing=None):
        self._existing = existing or {}
        self.writes = []

    def ensure_tables(self, control=None):
        pass

    def existing(self):
        return self._existing

    def write(self, rows, history, kinds, mark_dropped, run_id):
        self.writes.append({"rows": rows, "history": history, "kinds": kinds, "mark_dropped": mark_dropped})
        return len(rows)


class FakeGcs:
    def __init__(self):
        self.uploads = {}

    def ensure_bucket(self):
        pass

    def upload_text(self, name, text, content_type=None):
        self.uploads[name] = text
        return name


class Ctrl:
    def ensure_all(self):
        pass


def settings_with_export(tmp_path, **objects):
    s = base_settings(tmp_path)
    s["objects"] = {"export": {"local_dir": str(tmp_path / "sql"), "gcs": True}, **objects}
    return resolved(s)


def run(tmp_path, job=None, existing=None, reader=None, **objects):
    ts = settings_with_export(tmp_path, **objects)
    store, gcs = FakeStore(existing), FakeGcs()
    job = job or Job(objects=["views", "procedures"]).validate()
    res = run_objects(ts, {}, job, "run1", reader=reader or FakeObjectReader(), store=store, gcs=gcs,
                      control=Ctrl(), lock=None, ignore_locks=True, metadata=SimpleNamespace(log_stage_start=lambda *a, **k: None,
                                                                         log_stage_end=lambda *a, **k: None))
    return ts, res, store, gcs


# ---- object types / job -------------------------------------------------------------------
def test_kind_aliases_and_errors():
    assert normalize_kinds(["views", "SP"]) == ["view", "procedure"]
    assert normalize_kinds(["all"]) == ["view", "procedure", "function", "trigger"]
    assert normalize_kinds([], default=["views"]) == ["view"]
    assert type_codes(["function"]) == ["FN", "IF", "TF"]
    with pytest.raises(ObjectTypeError):
        normalize_kinds(["tables"])


def test_job_objects_only_and_validation():
    j = Job(objects=["procs"]).validate()
    assert j.objects == ["procedure"] and j.has_objects and not j.has_tables
    with pytest.raises(RunSpecError, match="object_names"):
        Job(all_tables=True, object_names=["dbo.x"]).validate()
    with pytest.raises(RunSpecError, match="raw objects"):
        Job().validate()
    spec = job_from_mapping({"databases": ["SalesDB"], "objects": "views,procedures", "object_names": "dbo.vw_*"})
    assert spec.objects == ["view", "procedure"] and spec.object_names == ["dbo.vw_*"]


# ---- catalog reader ------------------------------------------------------------------------
class FakeConn:
    def __init__(self, handler):
        self.handler = handler

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, clause, params=None):
        return SimpleNamespace(fetchall=lambda: self.handler(str(clause), params or {}))


def test_catalog_reader_index_and_details():
    R = SimpleNamespace

    def handler(sql, params):
        if "FROM sys.objects o" in sql and "parent_object_id" in sql:
            assert set(params.values()) >= {"V", "P"}
            return [R(object_id=1, schema_name="dbo", object_name="vw_Orders", type_code="V ", type_desc="VIEW",
                      create_date=None, modify_date=None, is_encrypted=0, parent_schema=None, parent_name=None),
                    R(object_id=4, schema_name="dbo", object_name="usp_Secret", type_code="P", type_desc="SQL_STORED_PROCEDURE",
                      create_date=None, modify_date=None, is_encrypted=1, parent_schema=None, parent_name=None),
                    R(object_id=9, schema_name="sys", object_name="x", type_code="V", type_desc="VIEW",
                      create_date=None, modify_date=None, is_encrypted=0, parent_schema=None, parent_name=None)]
        if "sys.sql_modules" in sql:
            return [R(object_id=1, definition=VIEW_SQL, uses_ansi_nulls=True, uses_quoted_identifier=True,
                      is_schema_bound=False, execute_as_principal_id=-1)]
        if "sys.parameters" in sql:
            return [R(object_id=4, parameter_id=1, parameter_name="@n", data_type="nvarchar", max_length=100,
                      precision=0, scale=0, is_output=False, has_default_value=False, is_readonly=False)]
        if "sys.columns" in sql:
            return [R(object_id=1, column_id=1, column_name="OrderId", data_type="int", max_length=4, precision=10,
                      scale=0, is_nullable=False)]
        if "sql_expression_dependencies" in sql:
            return [R(object_id=1, ref_schema="dbo", ref_name="Orders", ref_database=None, ref_server=None,
                      ref_type="U ")]
        raise AssertionError(sql)

    engine = SimpleNamespace(connect=lambda: FakeConn(handler))
    reader = oc.ObjectCatalogReader({"source": {"type": "mssql"}}, engine=engine)
    index = reader.read_index(["view", "procedure"])
    assert [(o["schema"], o["name"], o["kind"]) for o in index] == [("dbo", "vw_Orders", "view"),
                                                                    ("dbo", "usp_Secret", "procedure")]
    d = {o["name"]: o for o in reader.read_details(index)}
    assert d["vw_Orders"]["definition"] == VIEW_SQL and d["vw_Orders"]["definition_status"] == "OK"
    assert d["vw_Orders"]["references"][0] == {"schema": "dbo", "name": "Orders", "database": None, "server": None,
                                               "kind": "table", "type_code": "U"}
    assert d["usp_Secret"]["definition_status"] == "ENCRYPTED"
    assert d["usp_Secret"]["parameters"][0]["max_length"] == 50          # nvarchar bytes -> chars


def test_catalog_sql_parses_as_tsql():
    def ok(sql):
        sqlglot.parse_one(re.sub(r":\w+", "'1'", sql), read="tsql")
    ok(oc._INDEX_SQL.format(types=":t0, :t1", filter="AND s.name IN (:f_s0)"))
    for q in (oc._MODULES_SQL, oc._PARAMS_SQL, oc._COLUMNS_SQL, oc._DEPS_SQL):
        ok(q.format(ids="1, 2, 3"))


# ---- dependencies --------------------------------------------------------------------------
def test_dependency_levels_order_cycles_and_cross_db():
    lv = dependency_levels(list(DETAILS.values()), "SalesDB")
    assert lv[("dbo", "vw_orders")] == 0 and lv[("dbo", "usp_load")] == 1 and lv[("sales", "vw_report")] == 1
    a = detail(idx(10, "dbo", "a", "procedure"), "x", refs=[{"schema": "dbo", "name": "b"}])
    b = detail(idx(11, "dbo", "b", "procedure"), "x", refs=[{"schema": "dbo", "name": "a"}])
    cyc = dependency_levels([a, b], "db")
    assert set(cyc) == {("dbo", "a"), ("dbo", "b")} and all(v >= 0 for v in cyc.values())


# ---- pipeline ------------------------------------------------------------------------------
def test_first_run_writes_raw_files_bigquery_rows_and_manifest(tmp_path):
    ts, res, store, gcs = run(tmp_path)
    assert res["status"] == "SUCCESS" and res["objects"] == 4 and res["new"] == 4 and res["encrypted"] == 1
    rel = object_rel_path(ts, DETAILS[1])
    assert rel == "resourcegroup1/sqlserver1/salesdb/views/dbo.vw_Orders.sql"
    with open(os.path.join(tmp_path, "sql", *rel.split("/")), "rb") as f:
        assert f.read() == VIEW_SQL.encode("utf-8")                         # byte for byte, CRLF kept
    assert gcs.uploads[f"sql_objects/{rel}"] == VIEW_SQL
    assert "sql_objects/resourcegroup1/sqlserver1/salesdb/procedures/dbo.usp_Load.sql" in gcs.uploads
    assert not any("usp_Secret" in k for k in gcs.uploads)                  # encrypted: no file
    rows = {r["object_name"]: r for r in store.writes[0]["rows"]}
    assert rows["vw_Orders"]["translation_status"] == "PENDING" and rows["usp_Secret"]["translation_status"] == \
        "NOT_AVAILABLE"
    assert rows["vw_Report"]["references_cross_database"] is True and rows["usp_Load"]["dependency_level"] == 1
    assert rows["usp_Load"]["definition"] == PROC_SQL and len(rows["usp_Load"]["definition_sha256"]) == 64
    assert json.loads(rows["usp_Load"]["parameters_json"])[0]["name"] == "@From"
    assert len(store.writes[0]["history"]) == 4 and store.writes[0]["mark_dropped"] is True
    manifest = json.loads(gcs.uploads["sql_objects/resourcegroup1/sqlserver1/salesdb/manifest.json"])
    assert manifest["translated"] is False and len(manifest["objects"]) == 4
    assert [o["dependency_level"] for o in manifest["objects"]] == sorted(o["dependency_level"]
                                                                          for o in manifest["objects"])


def test_second_run_detects_changed_unchanged_dropped(tmp_path):
    import hashlib
    sha = hashlib.sha256(VIEW_SQL.encode()).hexdigest()
    existing = {
        ("view", "dbo", "vw_orders"): {"sha256": sha, "source_status": "PRESENT", "gcs_uri": "gs://x/a.sql",
                                       "schema": "dbo", "name": "vw_Orders"},
        ("procedure", "dbo", "usp_load"): {"sha256": "old", "source_status": "PRESENT", "gcs_uri": "gs://x/b.sql",
                                           "schema": "dbo", "name": "usp_Load"},
        ("procedure", "dbo", "usp_gone"): {"sha256": "z", "source_status": "PRESENT", "gcs_uri": None,
                                           "schema": "dbo", "name": "usp_Gone"},
        ("function", "dbo", "fn_other_kind"): {"sha256": "z", "source_status": "PRESENT", "gcs_uri": None,
                                               "schema": "dbo", "name": "fn_Other_Kind"},
    }
    _, res, store, gcs = run(tmp_path, existing=existing)
    assert (res["new"], res["changed"], res["unchanged"], res["dropped"]) == (2, 1, 1, 1)   # functions not touched
    assert not any(k.endswith("dbo.vw_Orders.sql") for k in gcs.uploads)       # unchanged: not re-uploaded
    hist = {(h["object_name"], h["change_status"]) for h in store.writes[0]["history"]}
    assert ("usp_Load", "CHANGED") in hist and ("usp_Gone", "DROPPED") in hist and ("vw_Orders", "UNCHANGED") not in hist


def test_patterns_limit_selection_and_disable_drop_marking(tmp_path):
    job = Job(objects=["views"], object_names=["sales.*"]).validate()
    _, res, store, _ = run(tmp_path, job=job,
                           existing={("view", "dbo", "vw_x"): {"sha256": "a", "source_status": "PRESENT"}})
    assert res["objects"] == 1 and res["dropped"] == 0 and store.writes[0]["mark_dropped"] is False


def test_stage_gcs_skips_bigquery(tmp_path):
    job = Job(objects=["views"], stage="gcs").validate()
    _, res, store, gcs = run(tmp_path, job=job)
    assert res["exported"] == 2 and store.writes == [] and res["bigquery_table"] is None and len(gcs.uploads) == 3


def test_failure_is_reported_not_raised(tmp_path):
    class Broken(FakeObjectReader):
        def read_details(self, objects):
            raise RuntimeError("login failed")
    _, res, _, _ = run(tmp_path, reader=Broken())
    assert res["status"] == "FAILED" and "login failed" in res["error"]


# ---- BigQuery store ------------------------------------------------------------------------
def test_merge_sql_parses_and_handles_drops():
    sql = build_objects_merge_sql("p.c.migration_sql_objects", "p.c.stg", mark_dropped=True)
    assert "NOT MATCHED BY SOURCE" in sql and "T.translated_sql = NULL" in sql
    assert "IS NOT DISTINCT FROM" in sql
    sqlglot.parse_one(build_objects_merge_sql("p.c.o", "p.c.s", mark_dropped=False), read="bigquery")


def test_store_write_uses_load_jobs_then_merge_then_history():
    client = mock.MagicMock()
    client.query.return_value = SimpleNamespace(result=lambda: None, num_dml_affected_rows=3)
    store = ObjectStore(resolved(base_settings()), client=client)
    assert store.staging_ref.endswith(".migration_control_asia_south1.stg__objects__sqlserver1_salesdb")
    store.write([{"object_name": "a"}], [{"object_name": "a"}], ["view"], mark_dropped=True, run_id="r")
    loads = client.load_table_from_json.call_args_list
    assert loads[0].args[1] == store.staging_ref and loads[1].args[1] == store.history_ref
    assert loads[1].kwargs["job_config"].write_disposition == "WRITE_APPEND"
    params = {p.name for p in client.query.call_args.kwargs["job_config"].query_parameters}
    assert params == {"rg", "srv", "db", "kinds", "now", "run"}
    client.delete_table.assert_called_with(store.staging_ref, not_found_ok=True)


# ---- planning / CLI ------------------------------------------------------------------------
def test_plan_object_job_across_databases_and_not_found():
    s = base_settings()
    targets = build_migration_targets(s)
    job = Job(objects=["views"], object_names=["SalesDB/dbo.vw_Orders", "dbo.vw_Nope"]).validate()
    plan = runner.plan_object_job(s, targets, job, reader_factory=FakeObjectReader)
    assert [runner.object_item_label(i) for i in plan.items] == ["ResourceGroup1/SQLServer1/SalesDB"]
    assert plan.items[0]["objects"][0]["name"] == "vw_Orders" and plan.unmatched == ["dbo.vw_Nope"]
    whole = runner.plan_object_job(s, targets, Job(objects=["views", "procedures"]).validate(), slim=True,
                                   reader_factory=FakeObjectReader)
    assert len(whole.items) == 3 and whole.items[0]["objects"] is None and whole.items[0]["object_count"] == 4
    assert runner.plan_job(s, targets, Job(objects=["views"]).validate(), reader_factory=FakeReader).items == []


@pytest.fixture
def cli_env(tmp_path):
    settings = base_settings(tmp_path)
    with mock.patch.object(main, "load_configs", return_value=(settings, {})), \
            mock.patch.object(runner, "CatalogReader", FakeReader), \
            mock.patch.object(main, "plan_job", side_effect=lambda *a, **k: runner.plan_job(
                *a, **{**k, "reader_factory": FakeReader})), \
            mock.patch.object(main, "plan_object_job", side_effect=lambda *a, **k: runner.plan_object_job(
                *a, **{**k, "reader_factory": FakeObjectReader})):
        yield settings


def test_cli_objects_dry_run_list_and_errors(cli_env, capsys):
    assert main.main(["--objects", "--database", "SalesDB", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "raw objects (views, procedures)" in out and "dbo.usp_Load" in out and "### job 'cli': mode" not in out
    assert main.main(["--views", "--list", "--object-names", "sales.*"]) == 0
    out = capsys.readouterr().out
    assert "sales.vw_Report" in out and "dbo.vw_Orders" not in out
    assert main.main(["--objects", "procs", "--object-names", "dbo.nope", "--dry-run", "--strict"]) == 1
    with pytest.raises(SystemExit):
        main.main(["--all", "--object-names", "dbo.x"])
    with pytest.raises(SystemExit):
        main.main(["--objects", "tables", "--dry-run"])


def test_cli_tables_and_objects_run_together(cli_env, capsys):
    with mock.patch.object(main, "provision", return_value={}), \
            mock.patch.object(main, "run_items", return_value=[{"table": "x", "status": "SUCCESS", "rows": 1}]) as ri, \
            mock.patch.object(main, "run_object_items", return_value=[{
                "database": "ResourceGroup1/SQLServer1/SalesDB", "status": "SUCCESS", "objects": 4,
                "by_kind": {"view": 2, "procedure": 2}, "new": 4, "changed": 0, "unchanged": 0, "dropped": 0,
                "encrypted": 1, "no_permission": 0, "local_dir": "/x", "gcs_prefix": None,
                "bigquery_table": "t"}]) as ro, \
            mock.patch.object(main, "generate_reports", return_value=[]):
        assert main.main(["--all", "--objects", "--database", "SalesDB", "--skip-provision"]) == 0
    assert len(ri.call_args.args[2]) == 4 and len(ro.call_args.args[2]) == 1
    assert "[2 procedures, 2 views]" in capsys.readouterr().out


def test_dag_conf_objects_only_vs_tables_and_objects():
    from src.orchestration.dag_tasks import jobs_from_conf
    only = jobs_from_conf({"databases": ["SalesDB"], "objects": ["views", "procedures"]})[0]
    assert only.has_objects and not only.has_tables
    both = jobs_from_conf({"all_tables": True, "objects": ["all"]})[0]
    assert both.has_tables and both.objects == ["view", "procedure", "function", "trigger"]
    assert jobs_from_conf({})[0].has_tables and not jobs_from_conf({})[0].has_objects


def test_store_retries_concurrent_merge():
    client = mock.MagicMock()
    ok = SimpleNamespace(result=lambda: None, num_dml_affected_rows=1)
    client.query.side_effect = [RuntimeError("Could not serialize access to table due to concurrent update"), ok]
    store = ObjectStore(resolved(base_settings()), client=client, sleep=lambda s: None)
    assert store.write([{"object_name": "a"}], [], ["view"], mark_dropped=False, run_id="r") == 1
    assert client.query.call_count == 2
