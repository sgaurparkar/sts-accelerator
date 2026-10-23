"""Table patterns, the selector, run specs and CLI job building."""
import pytest

import main
from src.selection.run_spec import Job, RunSpecError, load_spec, normalize_mode
from src.selection.table_selector import TablePatternError, TableSelector, expand_values, parse_pattern


@pytest.mark.parametrize("raw,expect", [
    ("Orders", (None, None, None, None, "Orders")),
    ("dbo.Orders", (None, None, None, "dbo", "Orders")),
    ("[dbo].[Order Details]", (None, None, None, "dbo", "Order Details")),
    ("[my.schema].[a]]b]", (None, None, None, "my.schema", "a]b")),
    ("SalesDB/dbo.Orders", (None, None, "SalesDB", "dbo", "Orders")),
    ("sqlserver1/SalesDB/dbo.*", (None, "sqlserver1", "SalesDB", "dbo", "*")),
    ("RG1/sqlserver1/SalesDB/*", ("RG1", "sqlserver1", "SalesDB", None, "*")),
])
def test_parse_pattern(raw, expect):
    p = parse_pattern(raw)
    assert (p.resource_group, p.server, p.database, p.schema, p.table) == expect


@pytest.mark.parametrize("bad", ["", "a.b.c", "a/b/c/d/e.f", "dbo.[x"])
def test_bad_patterns(bad):
    with pytest.raises(TablePatternError):
        parse_pattern(bad)


def test_expand_values_keeps_bracketed_commas():
    assert expand_values(["dbo.a,dbo.b", "[dbo].[x,y]", " c "]) == ["dbo.a", "dbo.b", "[dbo].[x,y]", "c"]


TABLES = [("dbo", "Orders"), ("dbo", "Customers"), ("dbo", "tmp_load"), ("sales", "Orders")]


def test_selector_scoping_wildcards_excludes_and_not_found():
    sel = TableSelector(["dbo.*", "SalesDB/sales.Orders", "HRDB/dbo.Missing"], exclude=["dbo.tmp_*"])
    assert sel.select("RG1", "srv1", "SalesDB", TABLES) == [("dbo", "Orders"), ("dbo", "Customers"),
                                                             ("sales", "Orders")]
    assert sel.select("RG1", "srv2", "HRDB", TABLES) == [("dbo", "Orders"), ("dbo", "Customers")]
    assert sel.unmatched_exact() == ["HRDB/dbo.Missing"]
    assert TableSelector().select("a", "b", "c", TABLES) == TABLES            # no include = everything
    assert TableSelector(schemas=["sales"]).select("a", "b", "c", TABLES) == [("sales", "Orders")]
    assert not TableSelector(["HRDB/dbo.x"]).applies_to_database("RG1", "srv1", "SalesDB")
    assert TableSelector(["orders"]).select("a", "b", "c", TABLES) == [("dbo", "Orders"), ("sales", "Orders")]


@pytest.mark.parametrize("alias,mode", [("delta", "incremental"), ("FULL", "full"), ("subset", "partial")])
def test_mode_aliases(alias, mode):
    assert normalize_mode(alias) == mode


@pytest.mark.parametrize("kwargs,msg", [
    ({"tables": []}, "choose tables"),
    ({"mode": "full", "where": "x=1"}, "need --mode partial"),
    ({"mode": "partial"}, "needs --where"),
    ({"mode": "partial", "date_from": "2026-01-01"}, "need --date-column"),
    ({"mode": "partial", "date_column": "d"}, "needs --from"),
    ({"mode": "partial", "date_column": "d", "date_from": "2026-02-01", "date_to": "2026-01-01"}, "before"),
    ({"mode": "partial", "date_column": "d", "date_from": "yesterday"}, "not a date"),
    ({"mode": "weird"}, "Unknown load mode"),
])
def test_job_validation(kwargs, msg):
    kwargs.setdefault("tables", ["t"])
    with pytest.raises(RunSpecError, match=msg):
        Job(**kwargs).validate()


def test_spec_file(tmp_path):
    (tmp_path / "t.txt").write_text("dbo.A\n# comment\ndbo.B, dbo.C\n")
    (tmp_path / "run.yaml").write_text("""
defaults: {mode: full}
jobs:
  - name: one
    databases: SalesDB
    all: true
  - name: two
    database: [HRDB]
    tables_file: t.txt
    mode: delta
  - name: three
    tables: [dbo.T]
    mode: partial
    date_column: d
    from: 2026-01-01
    to: 2026-02-01
    replace_range: true
""")
    one, two, three = load_spec(str(tmp_path / "run.yaml"))
    assert one.all_tables and one.databases == ["SalesDB"] and one.mode == "full"
    assert two.tables == ["dbo.A", "dbo.B", "dbo.C"] and two.mode == "incremental"
    assert three.partial_filter() == {"where": None, "date_column": "d", "from": "2026-01-01",
                                      "to": "2026-02-01", "replace_range": True}
    (tmp_path / "bad.yaml").write_text("jobs: [{tables: [x], colour: red}]")
    with pytest.raises(RunSpecError, match="unknown key"):
        load_spec(str(tmp_path / "bad.yaml"))


def _jobs(argv):
    return main.build_jobs(main.build_parser().parse_args(argv))


def test_cli_one_table_list_db_multi_db_and_modes():
    (j,) = _jobs(["--tables", "dbo.Orders"])
    assert j.tables == ["dbo.Orders"] and not j.all_tables and j.mode == "auto"
    (j,) = _jobs(["--table", "dbo.A", "dbo.B,dbo.C", "--mode", "full", "--force"])
    assert j.tables == ["dbo.A", "dbo.B", "dbo.C"] and j.force and j.mode == "full"
    (j,) = _jobs(["--all", "--database", "SalesDB"])
    assert j.all_tables and j.databases == ["SalesDB"]
    (j,) = _jobs(["--all", "--database", "SalesDB", "HRDB", "--mode", "incremental"])
    assert j.databases == ["SalesDB", "HRDB"] and j.mode == "incremental"
    (j,) = _jobs(["--tables", "*"])
    assert j.all_tables and j.tables == []
    (j,) = _jobs(["--tables", "dbo.Orders", "--mode", "partial", "--where", "Region = 'IN'"])
    assert j.partial_filter()["where"] == "Region = 'IN'"
    (j,) = _jobs(["--all", "--incremental-only"])
    assert j.only_modes == ["incremental"]
    with pytest.raises(RunSpecError):
        _jobs(["--spec", "x.yaml", "--all"])
