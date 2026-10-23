"""Offline tests for GCP-side provisioning (projects, buckets, datasets, control
tables, STS jobs). Every Google client is a fake — nothing touches GCP.
python -m pytest tests/test_gcp_resources.py"""
import copy
from types import SimpleNamespace
from unittest import mock

import pytest
from google.api_core import exceptions as gexc
from google.cloud import bigquery

import src.gcp.project_provisioner as pp
import src.gcs.gcs_manager as gm
from src.bigquery.bq_control_tables import BqControlTables
from src.sts.sts_client import StsClient
from src.sts.sts_job_manager import StsJobManager
from src.topology.targets import build_migration_targets, settings_for_target

BASE = {
    "azure_topology": {"source": "inventory", "inventory": [
        {"resource_group": "ResourceGroup1", "servers": [
            {"name": "SQLServer1", "location": "Central India", "databases": ["SalesDB"]}]}]},
    "source": {"type": "mssql"},
    "azure": {"storage_account": "stgacct", "container": "migration-staging"},
    "gcp": {"projects": {"billing_account": "AAAA-BBBB", "parent": "folders/42"}},
}


def resolved():
    s = copy.deepcopy(BASE)
    return settings_for_target(s, build_migration_targets(s)[0])


# ---- projects --------------------------------------------------------------------------------
def _provisioner(existing=None, billing_enabled=False, create_error=None, bq_failures=0):
    pp._ENSURED.clear()
    projects = mock.MagicMock()
    projects.get_project.side_effect = (lambda name: existing) if existing else gexc.PermissionDenied("no")
    if create_error:
        projects.create_project.side_effect = create_error
    billing = mock.MagicMock()
    billing.get_project_billing_info.return_value = SimpleNamespace(
        billing_enabled=billing_enabled, billing_account_name="billingAccounts/AAAA-BBBB" if billing_enabled else "")
    usage = mock.MagicMock()
    bq = mock.MagicMock()
    bq.list_datasets.side_effect = [gexc.Forbidden("not yet")] * bq_failures + [iter(())]
    p = pp.ProjectProvisioner(resolved(), projects_client=projects, billing_client=billing,
                              usage_client=usage, bq_client_factory=lambda pid: bq, sleep=lambda s: None)
    return p, projects, billing, usage


def test_missing_project_is_created_linked_and_enabled():
    p, projects, billing, usage = _provisioner(bq_failures=2)
    out = p.ensure_project("resourcegroup1", "ResourceGroup1", "ResourceGroup1")
    assert out["created"] is True
    body = projects.create_project.call_args.kwargs["project"]
    assert body.project_id == "resourcegroup1" and body.parent == "folders/42"
    assert body.labels["azure_resource_group"] == "resourcegroup1"
    assert billing.update_project_billing_info.call_args.kwargs["project_billing_info"].billing_account_name \
        == "billingAccounts/AAAA-BBBB"
    apis = usage.batch_enable_services.call_args.kwargs["request"]["service_ids"]
    assert {"bigquery.googleapis.com", "storage.googleapis.com", "storagetransfer.googleapis.com"} <= set(apis)
    # cached for the rest of the process
    assert p.ensure_project("resourcegroup1", "x", "x")["cached"] is True


def test_existing_project_is_reused():
    active = SimpleNamespace(state=SimpleNamespace(name="ACTIVE"))
    p, projects, billing, _ = _provisioner(existing=active, billing_enabled=True)
    assert p.ensure_project("resourcegroup1", "RG", "RG")["created"] is False
    projects.create_project.assert_not_called()
    billing.update_project_billing_info.assert_not_called()


def test_project_id_taken_globally():
    p, *_ = _provisioner(create_error=gexc.AlreadyExists("taken"))
    with pytest.raises(pp.ProvisioningError, match="id_prefix"):
        p.ensure_project("resourcegroup1", "RG", "RG")


def test_pending_delete_and_no_create():
    p, *_ = _provisioner(existing=SimpleNamespace(state=SimpleNamespace(name="DELETE_REQUESTED")))
    with pytest.raises(pp.ProvisioningError, match="pending deletion"):
        p.ensure_project("resourcegroup1", "RG", "RG")
    p, *_ = _provisioner()
    p.create_missing = False
    with pytest.raises(pp.ProvisioningError, match="create_missing is false"):
        p.ensure_project("resourcegroup1", "RG", "RG")


def test_missing_billing_account_config_fails_early():
    p, *_ = _provisioner()
    p.billing_account = None
    with pytest.raises(pp.ProvisioningError, match="billing_account"):
        p.ensure_project("resourcegroup1", "RG", "RG")


# ---- landing bucket --------------------------------------------------------------------------
def _gcs(lookup):
    gm._ENSURED_BUCKETS.clear()
    client = mock.MagicMock()
    client.lookup_bucket.side_effect = lookup
    sts = mock.MagicMock()
    sts.get_google_service_account.return_value = SimpleNamespace(
        account_email="project-123@storage-transfer-service.iam.gserviceaccount.com")
    return gm.GcsManager(resolved(), client=client, sts_client=sts), client


def test_bucket_created_in_target_project_and_location_with_sts_grant():
    m, client = _gcs([None])
    bucket = mock.MagicMock(location="ASIA-SOUTH1")
    bucket.get_iam_policy.return_value = SimpleNamespace(bindings=[])
    client.create_bucket.return_value = bucket
    m.ensure_bucket()
    kwargs = client.create_bucket.call_args.kwargs
    assert kwargs == {"project": "resourcegroup1", "location": "asia-south1"}
    roles = {b["role"] for b in bucket.set_iam_policy.call_args.args[0].bindings}
    assert roles == {"roles/storage.legacyBucketWriter", "roles/storage.objectViewer",
                     "roles/storage.legacyBucketReader"}
    new_bucket = client.bucket.return_value
    assert new_bucket.lifecycle_rules[0]["condition"] == {"age": 7, "matchesPrefix": ["parquet/"]}


def test_bucket_in_wrong_location_rejected():
    m, _ = _gcs([mock.Mock(location="US-EAST4")])
    with pytest.raises(RuntimeError, match="already exists in us-east4"):
        m.ensure_bucket()


def test_bucket_name_taken():
    m, client = _gcs([None, None])
    client.create_bucket.side_effect = gexc.Conflict("taken")
    with pytest.raises(RuntimeError, match="not available"):
        m.ensure_bucket()


# ---- BigQuery datasets / control tables -----------------------------------------------------
def test_datasets_created_with_location_labels_and_control_upgrade():
    client = mock.MagicMock()
    client.get_dataset.side_effect = gexc.NotFound("x")
    old_checkpoint = bigquery.Table("resourcegroup1.migration_control_asia_south1.migration_checkpoint",
                                    schema=[bigquery.SchemaField("table_name", "STRING", mode="REQUIRED"),
                                            bigquery.SchemaField("status", "STRING")])
    client.get_table.return_value = old_checkpoint
    bq = BqControlTables(resolved(), client=client)
    bq.ensure_dataset(bq.dataset)
    ds = client.create_dataset.call_args.args[0]
    assert ds.dataset_id == "sqlserver1_salesdb" and ds.location == "asia-south1"
    assert ds.labels["azure_server"] == "sqlserver1" and ds.labels["azure_resource_group"] == "resourcegroup1"
    bq.ensure_checkpoint_table("migration_checkpoint")
    updated = client.update_table.call_args.args[0]
    names = [f.name for f in updated.schema]
    assert {"resource_group", "source_server", "source_database", "last_pk_json"} <= set(names)


def test_existing_dataset_in_other_location_rejected():
    client = mock.MagicMock()
    client.get_dataset.return_value = SimpleNamespace(location="US")
    bq = BqControlTables(resolved(), client=client)
    with pytest.raises(RuntimeError, match="can't load GCS data across locations"):
        bq.ensure_dataset(bq.dataset)


def test_control_tables_include_lock_and_new_checkpoint_columns():
    client = mock.MagicMock()
    client.get_dataset.return_value = SimpleNamespace(location="asia-south1")
    client.get_table.side_effect = gexc.NotFound("x")
    bq = BqControlTables(resolved(), client=client)
    bq.ensure_all()
    created = {c.args[0].table_id: [f.name for f in c.args[0].schema] for c in client.create_table.call_args_list}
    assert set(created) == {"migration_pipeline_logs", "migration_checkpoint", "migration_audit", "migration_lock"}
    assert {"scope_key", "low_watermark_json", "high_watermark_json", "watermark_json"} <= set(
        created["migration_checkpoint"])
    assert {"source_rows", "target_rows", "reconciled"} <= set(created["migration_audit"])


# ---- Storage Transfer --------------------------------------------------------------------------
def test_sts_job_copies_one_chunk_folder_in_target_project(monkeypatch):
    monkeypatch.setenv("AZURE_SAS_TOKEN", "sas")
    rel = "parquet/resourcegroup1/sqlserver1/salesdb/dbo/Orders/full/chunk_00002_ab12cd34/"
    job = StsClient(resolved(), client=mock.MagicMock()).build_job(rel)
    spec = job.transfer_spec
    assert job.project_id == "resourcegroup1"
    assert spec.azure_blob_storage_data_source.path == rel
    assert spec.gcs_data_sink.bucket_name == "resourcegroup1-landing-asia-south1"
    assert spec.gcs_data_sink.path == rel


def test_sts_secret_lookup_prefers_account(monkeypatch):
    monkeypatch.setenv("AZURE_SAS_TOKEN", "shared")
    monkeypatch.setenv("AZURE_SAS_TOKEN_STGACCT", "per-account")
    job = StsClient(resolved(), client=mock.MagicMock()).build_job("x/")
    assert job.transfer_spec.azure_blob_storage_data_source.azure_credentials.sas_token == "per-account"


def test_sts_job_deleted_after_run_but_kept_on_timeout(monkeypatch):
    monkeypatch.setenv("AZURE_SAS_TOKEN", "sas")
    client = mock.MagicMock()
    client.create_transfer_job.return_value = SimpleNamespace(name="transferJobs/1")
    m = StsJobManager(resolved(), client=client)
    with mock.patch.object(m, "_run_and_wait", return_value={"status": "SUCCESS"}):
        m.transfer_directory("x/", 3)
    client.delete_transfer_job.assert_called_once()
    client.delete_transfer_job.reset_mock()
    with mock.patch.object(m, "_run_and_wait", return_value={"status": "TIMEOUT"}):
        m.transfer_directory("x/", 3)
    client.delete_transfer_job.assert_not_called()


def test_sts_summary_requires_every_expected_file():
    op = SimpleNamespace(counters=SimpleNamespace(objects_found_from_source=2, objects_copied_to_sink=2,
                                                  objects_from_source_skipped_by_sync=0,
                                                  objects_from_source_failed=0), error_breakdowns=[])
    assert StsJobManager._summarize("j", op, 2)["status"] == "SUCCESS"
    assert StsJobManager._summarize("j", op, 3)["status"] == "PARTIAL_FAILURE"
