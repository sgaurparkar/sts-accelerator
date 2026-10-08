"""
type_mapper.py

Maps SQL Server column types to BigQuery types, so bq_control_tables.py
can create tables with an explicit, correct schema instead of relying
on autodetect (which occasionally guesses wrong on edge cases like
dates stored as text).
"""

MSSQL_TO_BQ = {
    "int": "INT64", "bigint": "INT64", "smallint": "INT64", "tinyint": "INT64",
    "decimal": "NUMERIC", "numeric": "NUMERIC", "money": "NUMERIC", "smallmoney": "NUMERIC",
    "float": "FLOAT64", "real": "FLOAT64",
    "varchar": "STRING", "nvarchar": "STRING", "char": "STRING", "nchar": "STRING",
    "text": "STRING", "ntext": "STRING",
    "date": "DATE", "datetime": "TIMESTAMP", "datetime2": "TIMESTAMP",
    "smalldatetime": "TIMESTAMP", "datetimeoffset": "TIMESTAMP", "time": "TIME",
    "bit": "BOOL",
    "uniqueidentifier": "STRING",
    "varbinary": "BYTES", "binary": "BYTES", "image": "BYTES",
    # rowversion (a.k.a. timestamp) is an 8-byte binary counter, not a date/time
    "rowversion": "BYTES", "timestamp": "BYTES",
}

DECIMAL_TYPES = {"decimal", "numeric", "money", "smallmoney"}

# BigQuery NUMERIC is precision 38 / scale 9, i.e. at most 29 digits before
# the decimal point and at most 9 after it. Anything that needs more than
# that on either side (e.g. decimal(18,10) has scale 10; decimal(38,0) has
# 38 integer digits) has to be BIGNUMERIC or BigQuery will reject/truncate it.
BQ_NUMERIC_MAX_PRECISION = 38
BQ_NUMERIC_MAX_SCALE = 9
BQ_NUMERIC_MAX_INTEGER_DIGITS = BQ_NUMERIC_MAX_PRECISION - BQ_NUMERIC_MAX_SCALE   # 29


def map_type(source_type: str, precision: int = None, scale: int = None) -> str:
    key = source_type.split("(")[0].strip().lower()

    if key in DECIMAL_TYPES:
        if scale is not None and scale > BQ_NUMERIC_MAX_SCALE:
            return "BIGNUMERIC"
        if precision is not None and precision - (scale or 0) > BQ_NUMERIC_MAX_INTEGER_DIGITS:
            return "BIGNUMERIC"
        return "NUMERIC"

    # Fall back to STRING rather than failing outright — safer for a first
    # pass, and easy to spot/fix in the BigQuery schema after.
    return MSSQL_TO_BQ.get(key, "STRING")


def build_bigquery_schema(columns: list[dict]) -> list[dict]:
    """columns: [{'name':..., 'source_type':..., 'precision':..., 'scale':...}, ...]
    from schema_reader."""
    return [
        {
            "name": col["name"],
            "type": map_type(col["source_type"], col.get("precision"), col.get("scale")),
        }
        for col in columns
    ]
