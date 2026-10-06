import re
def dataset_for_schema(base_dataset: str, schema_name: str) -> str:
    if not schema_name:
        raise ValueError("schema_name is required to compute a per-schema BigQuery dataset")
    safe_schema = re.sub(r"[^A-Za-z0-9_]", "_", schema_name)
    return f"{safe_schema}"
