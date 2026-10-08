"""
schema_reader.py

Reads column names and types straight from SQL Server's
INFORMATION_SCHEMA.COLUMNS. Used by type_mapper.py to build the
BigQuery schema before load.
"""
from sqlalchemy import text


class SchemaReader:
    def __init__(self, config: dict):
        self.source_cfg = config["source"]
        if self.source_cfg.get("type") != "mssql":
            raise ValueError(
                f"Unsupported source.type '{self.source_cfg.get('type')}' — "
                "only 'mssql' is supported."
            )
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from src.config.connection import build_sql_engine
            self._engine = build_sql_engine(self.source_cfg)
        return self._engine

    def get_columns(self, table_name: str, schema: str = "dbo") -> list[dict]:
        """Returns [{'name', 'source_type', 'precision', 'scale'}, ...], ordinal-ordered."""
        query = text(
            """
            SELECT COLUMN_NAME, DATA_TYPE, NUMERIC_PRECISION, NUMERIC_SCALE
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_NAME = :table AND TABLE_SCHEMA = :schema
            ORDER BY ORDINAL_POSITION
            """
        )
        with self._get_engine().connect() as conn:
            rows = conn.execute(query, {"table": table_name, "schema": schema}).fetchall()
        return [
            {"name": r[0], "source_type": r[1], "precision": r[2], "scale": r[3]}
            for r in rows
        ]
