import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from unittest import mock
from azure.core.exceptions import ResourceExistsError
from src.azure import blob_uploader as bu


def _mk(monkeypatch, create):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING_EAST_US", "DefaultEndpointsProtocol=https;AccountName=a;AccountKey=Zm9v;EndpointSuffix=core.windows.net")
    cfg = {"azure": {"container": "parquet-backup"}, "source": {"region_slug": "east_us"}}
    with mock.patch.object(bu.BlobServiceClient, "from_connection_string") as f:
        f.return_value.create_container.side_effect = create
        return bu.BlobUploader(cfg), f.return_value


def test_creates_container(monkeypatch):
    _, c = _mk(monkeypatch, None)
    c.create_container.assert_called_once_with("parquet-backup")


def test_existing_container_ok(monkeypatch):
    _mk(monkeypatch, ResourceExistsError("x"))
