"""Type mapping, Parquet writing and typed checkpoint values."""
import datetime
import decimal
import uuid

import pyarrow.parquet as pq
import pytest

from src.common import values
from src.extraction.parquet_writer import rows_to_table, write_rows
from src.planner.type_mapper import decl, map_column
from tests.helpers import col


@pytest.mark.parametrize("c,staging,target,select", [
    (col("a", "int"), "INT64", "INT64", "[a]"),
    (col("a", "bit"), "BOOL", "BOOL", "[a]"),
    (col("a", "decimal", precision=18, scale=2), "NUMERIC", "NUMERIC", "[a]"),
    (col("a", "decimal", precision=38, scale=10), "BIGNUMERIC", "BIGNUMERIC", "[a]"),
    (col("a", "decimal", precision=38, scale=0), "BIGNUMERIC", "BIGNUMERIC", "[a]"),
    (col("a", "money"), "NUMERIC", "NUMERIC", "[a]"),
    (col("a", "datetime2"), "TIMESTAMP", "DATETIME", "[a]"),
    (col("a", "smalldatetime"), "TIMESTAMP", "DATETIME", "[a]"),
    (col("a", "datetimeoffset"), "TIMESTAMP", "TIMESTAMP", "CAST(SWITCHOFFSET([a], '+00:00') AS DATETIME2(7))"),
    (col("a", "date"), "DATE", "DATE", "[a]"),
    (col("a", "time"), "TIME", "TIME", "[a]"),
    (col("a", "uniqueidentifier"), "STRING", "STRING", "[a]"),
    (col("a", "timestamp"), "BYTES", "BYTES", "[a]"),
    (col("a", "geography"), "STRING", "STRING", "[a].STAsText()"),
    (col("a", "hierarchyid"), "STRING", "STRING", "[a].ToString()"),
    (col("a", "xml"), "STRING", "STRING", "CAST([a] AS NVARCHAR(MAX))"),
    (col("a", "sql_variant"), "STRING", "STRING", "CAST([a] AS NVARCHAR(4000))"),
    (col("odd]name", "somecustomtype"), "STRING", "STRING", "CONVERT(NVARCHAR(MAX), [odd]]name])"),
])
def test_map_column(c, staging, target, select):
    m = map_column(c)
    assert (m["staging_type"], m["target_type"], m["select_expr"]) == (staging, target, select)


def test_datetime_as_timestamp_keeps_old_behaviour():
    assert map_column(col("a", "datetime"), {"datetime_as": "TIMESTAMP"})["target_type"] == "TIMESTAMP"


@pytest.mark.parametrize("c,expect", [
    (col("a", "nvarchar", max_length=50), "nvarchar(50)"),
    (col("a", "varchar", max_length=-1), "varchar(max)"),
    (col("a", "char", max_length=10), "char(10)"),
    (col("a", "decimal", precision=18, scale=4), "decimal(18,4)"),
    (col("a", "datetime2", datetime_precision=3), "datetime2(3)"),
    (col("a", "datetime"), "datetime"),
    (col("a", "rowversion"), "binary(8)"),
])
def test_decl(c, expect):
    assert decl(c) == expect


def _cols(*cs):
    out = []
    for c in cs:
        m = map_column(c)
        m["bq_name"] = c["name"]
        out.append(m)
    return out


def test_parquet_types_match_staging_types(tmp_path):
    cols = _cols(col("i", "int"), col("d", "decimal", precision=18, scale=2), col("m", "money"),
                 col("big", "decimal", precision=38, scale=12), col("f", "float"), col("b", "bit"),
                 col("s", "nvarchar"), col("u", "uniqueidentifier"), col("bin", "varbinary"), col("dt", "date"),
                 col("tm", "time"), col("ts", "datetime2"), col("dto", "datetimeoffset"))
    rows = [
        (1, decimal.Decimal("12.5"), decimal.Decimal("3.1416"), decimal.Decimal("1.123456789012"), 1.5, True, "x",
         uuid.UUID("12345678-1234-5678-1234-567812345678"), b"\x00\x01", datetime.date(2026, 1, 2),
         datetime.time(10, 11, 12, 123456), datetime.datetime(9999, 12, 31, 23, 59, 59, 999999),
         datetime.datetime(2026, 1, 1, 12, 0, tzinfo=datetime.timezone(datetime.timedelta(hours=5, minutes=30)))),
        (None,) * 13,
    ]
    path, n, size = write_rows(rows, cols, str(tmp_path / "x" / "part_00000.parquet"))
    assert n == 2 and size > 0
    schema = pq.read_schema(path)
    types = {f.name: str(f.type) for f in schema}
    assert types == {"i": "int64", "d": "decimal128(18, 2)", "m": "decimal128(19, 4)", "big": "decimal128(38, 12)",
                     "f": "double", "b": "bool", "s": "string", "u": "string", "bin": "binary", "dt": "date32[day]",
                     "tm": "time64[us]", "ts": "timestamp[us]", "dto": "timestamp[us]"}
    t = pq.read_table(path).to_pylist()
    assert t[0]["d"] == decimal.Decimal("12.50") and t[0]["u"] == "12345678-1234-5678-1234-567812345678".upper()
    assert t[0]["ts"] == datetime.datetime(9999, 12, 31, 23, 59, 59, 999999)
    assert t[0]["dto"] == datetime.datetime(2026, 1, 1, 6, 30)          # converted to UTC
    assert all(v is None for v in t[1].values())


def test_bad_value_names_the_column():
    with pytest.raises(ValueError, match="Column i"):
        rows_to_table([("not a number",)], _cols(col("i", "int")))


@pytest.mark.parametrize("v", [None, 5, 2**62, 1.25, True, decimal.Decimal("1.230"), "abc",
                               datetime.datetime(2024, 1, 1, 10, 0, 0, 123456), datetime.date(2024, 2, 29),
                               datetime.time(1, 2, 3), b"\x00\xff"])
def test_typed_values_round_trip(v):
    assert values.load_one(values.dump_one(v)) == v
    assert values.loads(values.dumps({"k": v})) == {"k": v}


def test_rowversion_hex():
    assert values.rowversion_hex(b"\x00\x00\x00\x00\x00\x00\x07\xd1") == "0x00000000000007D1"
    assert values.rowversion_hex("0x01") == "0x01"
