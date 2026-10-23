"""
dataset_naming.py — BigQuery names for one migration target (settings resolved
by src/topology/targets.settings_for_target):

  data dataset      gcp.dataset            sqlserver1_salesdb     <- only migrated tables
  target table      bq_table               Orders / sales__Orders
  control dataset   gcp.control_dataset    migration_control_asia_south1
  staging table     stg__<dataset>__<table> in the CONTROL dataset
"""
from src.topology.naming import bq_table_name


def _require(settings: dict, key: str) -> str:
    value = (settings.get("gcp") or {}).get(key)
    if not value:
        raise RuntimeError(f"gcp.{key} is not resolved — build settings with "
                           "src/topology/targets.settings_for_target(settings, target).")
    return value


def data_dataset(settings: dict) -> str:
    return _require(settings, "dataset")


def control_dataset(settings: dict) -> str:
    return _require(settings, "control_dataset")


def target_table_name(settings: dict, table_cfg: dict) -> str:
    return table_cfg.get("bq_table") or bq_table_name(table_cfg["schema"], table_cfg["name"], settings.get("gcp"))


def staging_table_name(settings: dict, table_cfg: dict) -> str:
    return f"stg__{data_dataset(settings)}__{target_table_name(settings, table_cfg)}"[:1024]


def target_table_ref(settings: dict, table_cfg: dict) -> str:
    return f"{_require(settings, 'project_id')}.{data_dataset(settings)}.{target_table_name(settings, table_cfg)}"


def staging_table_ref(settings: dict, table_cfg: dict) -> str:
    return f"{_require(settings, 'project_id')}.{control_dataset(settings)}.{staging_table_name(settings, table_cfg)}"


def control_table_ref(settings: dict, table_name: str) -> str:
    return f"{_require(settings, 'project_id')}.{control_dataset(settings)}.{table_name}"
