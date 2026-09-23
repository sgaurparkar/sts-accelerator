"""
dataset_naming.py

Central place for the one naming rule this pipeline uses to keep each
source SQL Server schema (dbo, sales, ...) as its own separate
BigQuery dataset, instead of flattening every table into a single
dataset regardless of which schema it came from.

Used by bq_control_tables.py, bq_merge.py and report_generator.py so
none of them can drift out of sync on how a schema maps to a dataset
id — there is exactly one place this rule is written.

Operational/control tables (migration_pipeline_logs,
migration_checkpoint, migration_audit, migration_report) are NOT
schema-scoped — they log about every schema at once and stay in the
base dataset (gcp.bq_dataset from config/settings.yaml), with a
schema_name column on each row to filter/group by schema when needed.
Only the actual migrated data (target + staging tables) is split out
per schema.
"""
import re


def dataset_for_schema(base_dataset: str, schema_name: str) -> str:
    """e.g. dataset_for_schema("migrated_data", "dbo") -> "migrated_data_dbo"
    dataset_for_schema("migrated_data", "sales") -> "migrated_data_sales"

    BigQuery dataset ids only allow letters, numbers and underscores,
    so the schema name is sanitized the same way for every caller —
    a schema name with e.g. a hyphen in it can never produce a
    dataset id one caller accepts and another rejects.
    """
    if not schema_name:
        raise ValueError("schema_name is required to compute a per-schema BigQuery dataset")
    safe_schema = re.sub(r"[^A-Za-z0-9_]", "_", schema_name)
    return f"{safe_schema}"
