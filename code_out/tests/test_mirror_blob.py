import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.config import network_check as nc

BASE = {"source": {"host": "sql-x.database.windows.net"}, "azure": {"storage_account": "stsdemo"}}


def test_mirror_off_by_default_direct_checks_only_sql():
    s = {**BASE, "transfer": {"mode": "direct"}}
    assert nc.mirror_to_blob(s) is False
    assert [l for l, _ in nc.endpoints(s)] == ["Azure SQL server"]


def test_mirror_on_adds_blob_endpoint_to_private_check():
    s = {**BASE, "transfer": {"mode": "direct", "mirror_to_blob": True}}
    assert nc.mirror_to_blob(s) is True
    assert ("Azure Blob storage", "stsdemo.blob.core.windows.net") in nc.endpoints(s)


def test_blob_first_needs_mirror():
    s = {**BASE, "transfer": {"mode": "direct", "blob_first": True}}
    assert nc.blob_first(s) is False
    s["transfer"]["mirror_to_blob"] = True
    assert nc.blob_first(s) is True
