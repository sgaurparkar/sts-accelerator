"""The agreed hierarchy (multi-project mode):
   resource group -> project | server + database -> ONE dataset | schema + table -> table
   dbo.Orders -> Orders ; sales.Orders -> sales__Orders"""
import sys, pathlib
from unittest import mock
import pytest
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.bigquery.dataset_naming import bq_table_name, dataset_for_schema
from src.config import region_resolver as rr


def cfg(**mp):
    return {
        "source": {"database": "db"}, "azure": {"container": "c"},
        "gcp": {"project_id": "conn-proj", "gcs_bucket": "land", "bq_dataset": "migrated_data"},
        "multi_project": {"enabled": True, "billing_account": "A-B", "folder_id": "1",
                          "runner_service_account": "sa@x.iam.gserviceaccount.com", **mp},
        "regions": {"East US": {"host": "sql-server-01.database.windows.net", "database": "SalesDB",
                                "gcp_location": "us-east4", "resource_group": "rg-east-us",
                                "server": "sql-server-01"}},
    }


def test_table_names():
    assert bq_table_name("dbo", "Orders", flat=True) == "Orders"
    assert bq_table_name("DBO", "Orders", flat=True) == "Orders"
    assert bq_table_name("sales", "Orders", flat=True) == "sales__Orders"
    assert bq_table_name("my-schema", "Order Items", flat=True) == "my_schema__Order_Items"
    assert bq_table_name("sales", "Orders", flat=False) == "Orders"        # original layout unchanged


def test_dataset_is_server_db_when_flat():
    assert dataset_for_schema("x", "dbo", "sql_server_01_salesdb", flat=True) == "sql_server_01_salesdb"
    assert dataset_for_schema("x", "sales", "sql_server_01_salesdb", flat=True) == "sql_server_01_salesdb"
    assert dataset_for_schema("x", "sales", "sql_server_01_salesdb") == "sql_server_01_salesdb_sales"   # original


def test_project_name_equals_resource_group_with_empty_prefix():
    assert rr.project_id_for(cfg(project_prefix=""), "rg-east-us") == "rg-east-us"
    assert rr.project_id_for(cfg(project_prefix=None), "rg-east-us") == "mig-rg-east-us"   # None = default
    assert rr.project_id_for(cfg(), "rg-east-us") == "mig-rg-east-us"                       # key absent = default
    assert rr.project_id_for(cfg(project_prefix="mig"), "rg-east-us") == "mig-rg-east-us"


def test_empty_prefix_invalid_short_name_is_explained():
    with pytest.raises(rr.RegionError):
        rr.project_id_for(cfg(project_prefix=""), "rg1")       # 3 chars: not a valid project id


def test_resolved_settings_carry_flat_flag_and_names():
    r = rr.resolve_region(cfg(project_prefix=""), "East US")
    assert r["gcp"]["project_id"] == "rg-east-us"
    assert r["source"]["dataset_prefix"] == "sql_server_01_salesdb"
    assert r["source"]["flat_naming"] is True


def test_per_schema_layout_switch():
    r = rr.resolve_region(cfg(dataset_layout="per_schema"), "East US")
    assert r["source"]["flat_naming"] is False
    with pytest.raises(rr.RegionError):
        rr.resolve_region(cfg(dataset_layout="bogus"), "East US")


@pytest.fixture(autouse=True)
def fresh_src_modules():
    """Some other test files leave stubbed src.* modules in sys.modules; import the real ones for this test
    and put everything back afterwards."""
    saved = {m: sys.modules.pop(m) for m in [m for m in sys.modules if m.startswith("src.")]}
    yield
    for m in [m for m in sys.modules if m.startswith("src.")]:
        sys.modules.pop(m)
    sys.modules.update(saved)


def _tcfg(schema, name="Orders"):
    return {"schema": schema, "name": name, "columns": [{"name": "id", "type": "INT64", "source_type": "int"}], "primary_key": ["id"]}


@pytest.fixture
def settings():
    return rr.resolve_region(cfg(project_prefix=""), "East US")


def _control(settings):
    from src.bigquery import bq_control_tables as bct
    c = object.__new__(bct.BqControlTables)
    c.project_id, c.dataset = settings["gcp"]["project_id"], settings["gcp"]["bq_dataset"]
    src = settings["source"]
    c.region_slug = src.get("dataset_prefix") or src.get("region_slug")
    c.flat = bool(src.get("flat_naming"))
    c.client, c._ensured_datasets = mock.MagicMock(), set()
    return c


def _merge(settings):
    from src.bigquery import bq_merge as bm
    m = object.__new__(bm.BqMerge)
    m.project_id, m.base_dataset = settings["gcp"]["project_id"], settings["gcp"]["bq_dataset"]
    m.gcs_bucket = settings["gcp"]["gcs_bucket"]
    src = settings["source"]
    m.region_slug = src.get("dataset_prefix") or src.get("region_slug")
    m.flat = bool(src.get("flat_naming"))
    m.client = mock.MagicMock()
    return m


def test_control_tables_use_flat_names(settings):
    c = _control(settings)
    assert c.dataset_for_table(_tcfg("dbo")) == "sql_server_01_salesdb"
    assert c.dataset_for_table(_tcfg("sales")) == "sql_server_01_salesdb"
    assert c.table_name_for(_tcfg("dbo")) == "Orders"
    assert c.table_name_for(_tcfg("sales")) == "sales__Orders"
    with mock.patch.object(c, "_ensure_data_table"), mock.patch.object(c, "ensure_dataset"):
        assert c.ensure_target_table(_tcfg("sales")) == "rg-east-us.sql_server_01_salesdb.sales__Orders"
        assert c.ensure_staging_table(_tcfg("sales")) == "rg-east-us.sql_server_01_salesdb.sales__Orders_staging"
        assert c.ensure_staging_table(_tcfg("dbo")) == "rg-east-us.sql_server_01_salesdb.Orders_staging"


def test_merge_targets_flat_names(settings):
    m = _merge(settings)
    m.client.query.return_value = mock.Mock(num_dml_affected_rows=3)
    out = m.merge_batch_into_target(_tcfg("sales"))
    sql = m.client.query.call_args[0][0]
    assert "MERGE `rg-east-us.sql_server_01_salesdb.sales__Orders` T" in sql
    assert "USING `rg-east-us.sql_server_01_salesdb.sales__Orders_staging` S" in sql
    assert out["dataset"] == "sql_server_01_salesdb"


def test_load_uri_still_uses_source_schema_and_table(settings):
    m = _merge(settings)
    ref = m.load_batch_to_staging(_tcfg("sales"), 0)
    uri = m.client.load_table_from_uri.call_args[0][0]
    assert ref == "rg-east-us.sql_server_01_salesdb.sales__Orders_staging"
    assert uri.endswith("/parquet/sales/Orders/Orders_part0000.parquet")     # GCS / Blob paths unchanged


def test_single_project_mode_unchanged():
    c = _control({"gcp": {"project_id": "p", "bq_dataset": "migrated_data"}, "source": {"region_slug": "east_us"}})
    assert c.dataset_for_table(_tcfg("sales")) == "east_us_sales"
    assert c.table_name_for(_tcfg("sales")) == "Orders"
