"""

type_mapper.py
 
Maps SQL Server column types to BigQuery types, so bq_control_tables.py

can create tables with an explicit, correct schema instead of relying

on autodetect (which occasionally guesses wrong on edge cases like

dates stored as text). SQLite mapping has been removed along with the

rest of the SQLite demo path.

"""
 
MSSQL_TO_BQ = {

    "int": "INT64", "bigint": "INT64", "smallint": "INT64", "tinyint": "INT64",

    "decimal": "NUMERIC", "numeric": "NUMERIC", "money": "NUMERIC", "smallmoney": "NUMERIC",

    "float": "FLOAT64", "real": "FLOAT64",

    "varchar": "STRING", "nvarchar": "STRING", "char": "STRING", "nchar": "STRING", "text": "STRING", "ntext": "STRING",

    "date": "DATE", "datetime": "TIMESTAMP", "datetime2": "TIMESTAMP",

    "smalldatetime": "TIMESTAMP", "datetimeoffset": "TIMESTAMP", "time": "TIME",

    "bit": "BOOL",

    "uniqueidentifier": "STRING",

    "varbinary": "BYTES", "binary": "BYTES",

}
 
 
DECIMAL_TYPES = {"decimal", "numeric", "money", "smallmoney"}
 
# BigQuery NUMERIC caps out at precision 38 / scale 9. Anything wider

# (e.g. SQL Server decimal(18,10), which is scale 10) has to be

# BIGNUMERIC or BigQuery will reject/truncate it.

BQ_NUMERIC_MAX_PRECISION = 38

BQ_NUMERIC_MAX_SCALE = 9
 
 
def map_type(source_type: str, precision: int = None, scale: int = None) -> str:

    key = source_type.split("(")[0].strip().lower()
 
    if key in DECIMAL_TYPES:

        if scale is not None and scale > BQ_NUMERIC_MAX_SCALE:

            return "BIGNUMERIC"

        if precision is not None and precision > BQ_NUMERIC_MAX_PRECISION:

            return "BIGNUMERIC"

        return "NUMERIC"
 
    mapped = MSSQL_TO_BQ.get(key)

    if mapped is None:

        # Fall back to STRING rather than failing outright — safer for a

        # first pass, and easy to spot/fix in the BigQuery schema after.

        return "STRING"

    return mapped
 
 
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
 