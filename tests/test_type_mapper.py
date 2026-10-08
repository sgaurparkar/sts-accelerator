import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from src.planner.type_mapper import map_type, build_bigquery_schema


def test_mssql_types():
    assert map_type("varchar(50)") == "STRING"
    assert map_type("int") == "INT64"
    assert map_type("datetime2") == "TIMESTAMP"
    assert map_type("uniqueidentifier") == "STRING"
    assert map_type("bit") == "BOOL"


def test_unknown_type_falls_back_to_string():
    assert map_type("xml") == "STRING"


def test_build_bigquery_schema():
    columns = [{"name": "id", "source_type": "int"}, {"name": "name", "source_type": "varchar(100)"}]
    schema = build_bigquery_schema(columns)
    assert schema == [{"name": "id", "type": "INT64"}, {"name": "name", "type": "STRING"}]


def test_decimal_widths():
    assert map_type("decimal", 18, 2) == "NUMERIC"
    assert map_type("money", 19, 4) == "NUMERIC"
    assert map_type("decimal", 38, 9) == "NUMERIC"        # 29 integer digits: the NUMERIC maximum
    assert map_type("decimal", 18, 10) == "BIGNUMERIC"     # scale beyond 9
    assert map_type("decimal", 38, 0) == "BIGNUMERIC"      # 38 integer digits don't fit NUMERIC
    assert map_type("numeric", 38, 5) == "BIGNUMERIC"      # 33 integer digits


def test_binary_types():
    for t in ("varbinary", "binary", "image", "rowversion", "timestamp"):
        assert map_type(t) == "BYTES"


if __name__ == "__main__":
    test_mssql_types()
    test_unknown_type_falls_back_to_string()
    test_build_bigquery_schema()
    test_decimal_widths()
    test_binary_types()
    print("All tests passed.")
