"""Offline edge-case tests for region handling:  python -m pytest tests/test_region_resolver.py
(cloud SDKs are stubbed, nothing touches Azure/GCP)."""
import copy
import sys
import types
from unittest import mock

import pytest

# ---- stubs so the modules import without cloud SDKs / drivers -------------------------
class _Conflict(Exception): ...
class _Forbidden(Exception): ...
class _NotFound(Exception): ...

_exc = types.ModuleType("google.api_core.exceptions")
_exc.Conflict, _exc.Forbidden, _exc.NotFound = _Conflict, _Forbidden, _NotFound
for name in ("google", "google.api_core", "google.cloud", "google.cloud.bigquery",
             "google.cloud.storage", "google.cloud.storage_transfer_v1", "azure", "azure.storage",
             "azure.storage.blob", "dotenv", "sqlalchemy", "sqlalchemy.engine"):
    sys.modules.setdefault(name, mock.MagicMock())
sys.modules["google.api_core"].exceptions = _exc
sys.modules["google.api_core.exceptions"] = _exc

from src.config import region_resolver as rr  # noqa: E402
from src.config.connection import regional_env  # noqa: E402
from src.bigquery.dataset_naming import dataset_for_schema  # noqa: E402
import src.gcs.gcs_manager as gm  # noqa: E402

BASE = {
    "source": {"type": "mssql", "host": "legacy.database.windows.net", "database": "sales_poc"},
    "azure": {"storage_account": "acct", "container": "c"},
    "gcp": {"project_id": "p", "gcs_bucket": "migration-landing-bucket", "bq_dataset": "migrated_data"},
    "regions": {
        "East US": {"host": "e.database.windows.net", "gcp_location": "us-east4"},
        "West US": {"host": "w.database.windows.net", "gcp_location": "us-west2"},
        "West US 2": {"host": "w2.database.windows.net", "gcp_location": "us-west1"},
        "Brazil South": {"host": "<server-name>.database.windows.net", "gcp_location": "southamerica-east1"},
        "Germany West Central": {"host": "g.database.windows.net", "gcp_location": "europe-west3"},
    },
}


def cfg(**changes):
    s = copy.deepcopy(BASE)
    s.update(changes)
    return s


# ---- matching ---------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["East US", "east us", "EAST-US", "east_us", "eastus", "  East   US  "])
def test_forgiving_match(text):
    assert rr.resolve_region(cfg(), text)["source"]["region"] == "East US"


def test_west_us_is_not_west_us_2():
    assert rr.resolve_region(cfg(), "west us")["source"]["host"] == "w.database.windows.net"
    assert rr.resolve_region(cfg(), "west us 2")["source"]["host"] == "w2.database.windows.net"


def test_no_prefix_matching():
    with pytest.raises(rr.RegionError, match="Unknown region"):
        rr.resolve_region(cfg(), "west")


def test_cli_words_list_and_blank():
    assert rr.clean_requested(["North", "Central", "US"]) == "North Central US"
    assert rr.clean_requested("   ") is None and rr.clean_requested(None) is None


# ---- required / unknown / legacy ----------------------------------------------------------
def test_missing_region_is_error():
    with pytest.raises(rr.RegionError, match="--region is required"):
        rr.resolve_region(cfg(), None)
    with pytest.raises(rr.RegionError, match="--region is required"):
        rr.regions_to_run(cfg(), "  ")


def test_region_without_regions_configured_is_error_not_ignored():
    s = cfg(); del s["regions"]
    with pytest.raises(rr.RegionError, match="no `regions:`"):
        rr.resolve_region(s, "East US")
    assert rr.regions_to_run(s, None) == [None]
    assert rr.resolve_region(s, None) is s                      # legacy mode untouched


# ---- host validation ------------------------------------------------------------------------
def test_placeholder_and_malformed_hosts():
    with pytest.raises(rr.RegionError, match="placeholder"):
        rr.resolve_region(cfg(), "brazil south")
    for bad in ("https://x.database.windows.net", "x.database.windows.net/db", "my server.net", ""):
        s = cfg(); s["regions"]["East US"]["host"] = bad
        with pytest.raises(rr.RegionError):
            rr.resolve_region(s, "east us")


def test_entry_shorthand_and_none():
    s = cfg(); s["regions"]["East US"] = "short.database.windows.net"
    with pytest.raises(rr.RegionError, match="gcp_location"):   # shorthand has no location
        rr.resolve_region(s, "east us")
    s["regions"]["East US"] = None
    with pytest.raises(rr.RegionError, match="placeholder"):
        rr.resolve_region(s, "east us")


# ---- location / names -----------------------------------------------------------------------
def test_gcp_location_required_and_validated():
    s = cfg(); del s["regions"]["East US"]["gcp_location"]
    with pytest.raises(rr.RegionError, match="gcp_location"):
        rr.resolve_region(s, "east us")
    s["regions"]["East US"]["gcp_location"] = "mars-1"
    s["regions"]["East US"]["gcp_location"] = "not a region"
    with pytest.raises(rr.RegionError, match="invalid gcp_location"):
        rr.resolve_region(s, "east us")
    s["regions"]["East US"]["gcp_location"] = "US"              # multi-region is fine
    assert rr.resolve_region(s, "east us")["gcp"]["location"] == "US"


def test_resolved_names_and_input_not_mutated():
    s = cfg(); before = copy.deepcopy(s)
    r = rr.resolve_region(s, "germany west central")
    assert r["gcp"]["gcs_bucket"] == "migration-landing-bucket-germany-west-central"
    assert r["gcp"]["bq_dataset"] == "migrated_data_germany_west_central"
    assert r["gcp"]["location"] == "europe-west3" and r["source"]["region_slug"] == "germany_west_central"
    assert s == before


def test_overrides_and_too_long_bucket():
    s = cfg(); s["regions"]["East US"].update(gcs_bucket="my-east-bucket", bq_dataset="east_ctl", database="otherdb")
    r = rr.resolve_region(s, "east us")
    assert (r["gcp"]["gcs_bucket"], r["gcp"]["bq_dataset"], r["source"]["database"]) == ("my-east-bucket", "east_ctl", "otherdb")
    s = cfg(); s["gcp"]["gcs_bucket"] = "x" * 60
    with pytest.raises(rr.RegionError, match="not valid"):
        rr.resolve_region(s, "east us")


def test_duplicate_after_normalization_and_missing_sections():
    s = cfg(); s["regions"]["east-us"] = {"host": "h", "gcp_location": "us-east4"}
    with pytest.raises(rr.RegionError, match="same region"):
        rr.regions_to_run(s, "east us")
    s = cfg(); del s["gcp"]["gcs_bucket"]
    with pytest.raises(rr.RegionError, match="gcp.gcs_bucket"):
        rr.resolve_region(s, "east us")


def test_resolving_twice_is_safe():
    r = rr.resolve_region(cfg(), "east us")
    assert rr.resolve_region(r, "East US") is r
    assert rr.resolve_region(r, None) is r
    with pytest.raises(rr.RegionError, match="already resolved"):
        rr.resolve_region(r, "west us")


# ---- "all" ---------------------------------------------------------------------------------
def test_all_skips_placeholder_regions():
    names = rr.regions_to_run(cfg(), "ALL")
    assert "Brazil South" not in names and len(names) == 4
    s = cfg()
    for v in s["regions"].values():
        v["host"] = "<x>"
    with pytest.raises(rr.RegionError, match="No region has"):
        rr.regions_to_run(s, "all")
    with pytest.raises(rr.RegionError, match="must be expanded"):
        rr.resolve_region(cfg(), "all")


# ---- secrets / naming -----------------------------------------------------------------------
def test_regional_env_precedence_and_blank(monkeypatch):
    monkeypatch.setenv("AZURE_SQL_PASSWORD", "shared")
    assert regional_env("AZURE_SQL_PASSWORD", "east_us") == "shared"
    monkeypatch.setenv("AZURE_SQL_PASSWORD_EAST_US", "regional")
    assert regional_env("AZURE_SQL_PASSWORD", "east_us") == "regional"
    assert regional_env("AZURE_SQL_PASSWORD", "west_us") == "shared"
    monkeypatch.setenv("AZURE_SQL_PASSWORD_EAST_US", "   ")        # blank = unset
    assert regional_env("AZURE_SQL_PASSWORD", "east_us") == "shared"


def test_dataset_naming():
    assert dataset_for_schema("migrated_data", "dbo") == "dbo"
    assert dataset_for_schema("migrated_data", "my-schema", "east_us") == "east_us_my_schema"
    with pytest.raises(ValueError):
        dataset_for_schema("x", "")


# ---- GCS bucket handling --------------------------------------------------------------------
def _mgr(location="us-east4", slug="east_us"):
    gm._ENSURED_BUCKETS.clear()
    m = gm.GcsManager({"gcp": {"project_id": "p", "gcs_bucket": "b-east-us", "location": location},
                       "source": {"region_slug": slug}})
    m.client = mock.MagicMock()
    return m


def test_bucket_created_when_missing():
    m = _mgr(); m.client.lookup_bucket.return_value = None
    m.client.create_bucket.return_value = mock.Mock(location="US-EAST4")
    m.ensure_bucket()
    assert m.client.create_bucket.call_args.kwargs["location"] == "us-east4"


def test_bucket_existing_same_location_untouched_and_cached():
    m = _mgr(); m.client.lookup_bucket.return_value = mock.Mock(location="US-EAST4")
    m.ensure_bucket(); m.ensure_bucket()
    m.client.create_bucket.assert_not_called()
    assert m.client.lookup_bucket.call_count == 1


def test_bucket_wrong_location_rejected():
    m = _mgr(); m.client.lookup_bucket.return_value = mock.Mock(location="ASIA-SOUTH1")
    with pytest.raises(RuntimeError, match="already exists in asia-south1"):
        m.ensure_bucket()


def test_bucket_create_race_and_name_taken():
    m = _mgr(); m.client.create_bucket.side_effect = _Conflict("exists")
    m.client.lookup_bucket.side_effect = [None, mock.Mock(location="us-east4")]   # raced: appears on 2nd look
    m.ensure_bucket()
    m = _mgr(); m.client.create_bucket.side_effect = _Conflict("taken")
    m.client.lookup_bucket.side_effect = [None, None]
    with pytest.raises(RuntimeError, match="not available"):
        m.ensure_bucket()


def test_bucket_forbidden_messages():
    m = _mgr(); m.client.lookup_bucket.side_effect = _Forbidden("no")
    with pytest.raises(RuntimeError, match="another"):
        m.ensure_bucket()
    m = _mgr(); m.client.lookup_bucket.return_value = None; m.client.create_bucket.side_effect = _Forbidden("no")
    with pytest.raises(RuntimeError, match="No permission to create"):
        m.ensure_bucket()


def test_no_bucket_work_outside_region_mode():
    m = _mgr(location=None, slug=None); m.ensure_bucket()
    m.client.lookup_bucket.assert_not_called()
