import re


def dataset_for_schema(base_dataset: str, schema_name: str, region_slug: str | None = None) -> str:
    """BigQuery dataset a source schema's tables land in.

    Without a region: the schema name itself (dbo -> `dbo`), as before.
    With a region (see src/config/region_resolver.py): the region is part of
    the name, so every Azure region gets its own datasets and two regional
    servers that both have dbo.customers never share one BigQuery table
    (North Central US + dbo -> `north_central_us_dbo`).
    """
    if not schema_name:
        raise ValueError("schema_name is required to compute a per-schema BigQuery dataset")
    safe_schema = re.sub(r"[^A-Za-z0-9_]", "_", schema_name)
    if region_slug:
        return f"{region_slug}_{safe_schema}"
    return f"{safe_schema}"
