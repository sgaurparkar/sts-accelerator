import re


def _safe(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_]", "_", str(text))


def dataset_for_schema(base_dataset: str, schema_name: str, region_slug: str | None = None,
                       flat: bool = False) -> str:
    """BigQuery dataset a source schema's tables land in.

    flat=False (single-project / region mode, the original layout): one dataset PER SCHEMA.
      Without a region: the schema name itself (dbo -> `dbo`).
      With a region: `<region>_<schema>` (North Central US + dbo -> `north_central_us_dbo`).

    flat=True (multi-project mode, the agreed hierarchy): ONE dataset per SQL server + database,
      i.e. region_slug is already `<server>_<database>` (sqlserver1_salesdb). Every schema of that
      database lands in this one dataset; the schema is carried in the TABLE name instead
      (see bq_table_name).
    """
    if not schema_name:
        raise ValueError("schema_name is required to compute a per-schema BigQuery dataset")
    if flat and region_slug:
        return region_slug
    safe_schema = _safe(schema_name)
    if region_slug:
        return f"{region_slug}_{safe_schema}"
    return f"{safe_schema}"


def bq_table_name(schema_name: str, table_name: str, flat: bool = False) -> str:
    """BigQuery table name for a source table.

    flat=False: the source table name unchanged (original behaviour).
    flat=True : schema `dbo` is dropped -> `Orders`; any other schema is joined with a double
                underscore -> `sales__Orders`. Characters BigQuery does not allow become `_`.
    """
    if not flat:
        return table_name
    name = _safe(table_name)
    if not schema_name or str(schema_name).strip().lower() == "dbo":
        return name
    return f"{_safe(schema_name)}__{name}"
