"""Descriptive Parquet file names (src/topology/paths.py)."""
import pytest

from src.topology.paths import (
    MAX_FILENAME_BYTES, batch_filename, descriptive_filename, file_component, parse_filename, run_tag,
)

SETTINGS = {"source": {"resource_group": "INGREDION-DEV-RG-CENTRAL-INDIA", "server": "sqlsrv-sales01",
                       "database": "SalesDB"}, "extraction": {}}


def test_name_is_broad_to_narrow_and_parses_back():
    name = batch_filename(7, SETTINGS, "dbo", "order_line_items", "full", "run_20261022T074512Z_1a2b3c4d")
    assert name == ("rg-INGREDION-DEV-RG-CENTRAL-INDIA__srv-sqlsrv-sales01__db-SalesDB__sch-dbo__"
                    "tbl-order_line_items__mode-full__run-20261022T074512Z__part-00007.parquet")
    p = parse_filename("gs://b/parquet/x/" + name)
    assert (p["rg"], p["srv"], p["db"], p["sch"], p["tbl"]) == (
        "INGREDION-DEV-RG-CENTRAL-INDIA", "sqlsrv-sales01", "SalesDB", "dbo", "order_line_items")
    assert (p["mode"], p["run"], p["part"]) == ("full", "20261022T074512Z", 7)


@pytest.mark.parametrize("raw", ["Order Details", "order__line_items", "[odd].[name]", "_tmp", "Ünïcode", "a]b"])
def test_values_never_contain_the_field_separator(raw):
    v = file_component(raw)
    assert "__" not in v and v
    name = descriptive_filename("rg", "srv", "db", "dbo", raw, "full", "20261022T074512Z", 0)
    assert parse_filename(name)["tbl"] == v


def test_different_source_names_never_share_a_file_name():
    names = {file_component(x) for x in ("Order Details", "Order_Details", "Order-Details", "order details")}
    assert len(names) == 4


def test_unchanged_names_get_no_hash():
    assert file_component("Orders") == "Orders"
    assert file_component("order_line_items") == "order_line_items"


def test_long_names_are_shortened_with_a_hash():
    long = "T" * 128
    name = descriptive_filename("r" * 90, "s" * 63, "d" * 128, "s" * 128, long, "partial:0123456789ab", "20261022T074512Z", 3)
    assert len(name.encode()) <= MAX_FILENAME_BYTES
    other = descriptive_filename("r" * 90, "s" * 63, "d" * 128, "s" * 128, long[:-1] + "U", "partial:0123456789ab",
                                 "20261022T074512Z", 3)
    assert name != other


def test_partial_scope_and_sorting():
    a = batch_filename(2, SETTINGS, "dbo", "T", "partial:abc123", "run_20261022T074512Z_aaaa0000")
    b = batch_filename(10, SETTINGS, "dbo", "T", "partial:abc123", "run_20261022T074512Z_aaaa0000")
    assert "__mode-partial-abc123__" in a and sorted([b, a]) == [a, b]


def test_short_naming_and_missing_context_keep_old_name():
    assert batch_filename(4) == "part_00004.parquet"
    short = {**SETTINGS, "extraction": {"file_naming": "short"}}
    assert batch_filename(4, short, "dbo", "T", "full", run_tag()) == "part_00004.parquet"


def test_bad_names_are_rejected():
    with pytest.raises(ValueError):
        parse_filename("part_00000.parquet")
    with pytest.raises(ValueError):
        batch_filename(0, {**SETTINGS, "extraction": {"file_naming": "fancy"}}, "dbo", "T", "full", run_tag())
