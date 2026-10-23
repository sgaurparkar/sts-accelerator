"""
shadow_table_naming.py — names of the synthetic-key shadow tables that the
optional no_pk_strategy "shadow_table" creates in the SOURCE database.

Kept within SQL Server's 128-character identifier limit: long table names are
shortened and get a stable hash suffix, so two long names never collide.
"""
import hashlib

SHADOW_TABLE_PREFIX = "__migration_rownum__"
_MAX_IDENTIFIER = 128


def is_shadow_table(table_name: str) -> bool:
    return str(table_name).startswith(SHADOW_TABLE_PREFIX)


def shadow_table_name(table_name: str) -> str:
    name = f"{SHADOW_TABLE_PREFIX}{table_name}"
    if len(name) <= _MAX_IDENTIFIER:
        return name
    digest = hashlib.sha1(str(table_name).encode("utf-8")).hexdigest()[:8]
    room = _MAX_IDENTIFIER - len(SHADOW_TABLE_PREFIX) - len(digest) - 1
    return f"{SHADOW_TABLE_PREFIX}{table_name[:room]}_{digest}"
