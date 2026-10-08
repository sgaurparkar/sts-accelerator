"""
shadow_table_naming.py

Single source of truth for the naming convention used by the
synthetic-key shadow tables SqlExtractor creates for no-PK tables
(see sql_extractor.py's _ensure_synthetic_key_table). Both
table_discovery.py (to exclude them from migration) and
sql_extractor.py (to create/drop them) import from here, so the two
can never drift out of sync with each other.
"""

SHADOW_TABLE_PREFIX = "__migration_rownum__"


def is_shadow_table(table_name: str) -> bool:
    """True for any internal shadow table this pipeline created."""
    return table_name.startswith(SHADOW_TABLE_PREFIX)


def shadow_table_name(table_name: str) -> str:
    return f"{SHADOW_TABLE_PREFIX}{table_name}"
