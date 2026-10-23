import pytest
from src.config import network_check as nc

SETTINGS = {"source": {"host": "sql-poc-eastus.database.windows.net", "region": "East US"},
            "azure": {"storage_account": "stpocnaeastus"}, "network": {"require_private": True}}


def fake(mapping):
    def _resolve(host):
        v = mapping[host]
        if isinstance(v, Exception):
            raise v
        return v
    return _resolve


@pytest.fixture(autouse=True)
def reset(monkeypatch):
    nc._checked.clear()
    monkeypatch.delenv("REQUIRE_PRIVATE_PATH", raising=False)


def test_private_addresses_pass(monkeypatch):
    monkeypatch.setattr(nc, "_resolve", fake({"sql-poc-eastus.database.windows.net": ["10.32.1.4"],
                                              "stpocnaeastus.blob.core.windows.net": ["10.32.1.5"]}))
    assert all(r["private"] for r in nc.check_private_path(SETTINGS))
    nc.ensure_private_path(SETTINGS)


def test_public_address_is_refused(monkeypatch):
    monkeypatch.setattr(nc, "_resolve", fake({"sql-poc-eastus.database.windows.net": ["20.1.2.3"],
                                              "stpocnaeastus.blob.core.windows.net": ["10.32.1.5"]}))
    with pytest.raises(RuntimeError, match="Not using the VPN tunnel"):
        nc.ensure_private_path(SETTINGS)


def test_unresolvable_is_refused(monkeypatch):
    monkeypatch.setattr(nc, "_resolve", fake({"sql-poc-eastus.database.windows.net": OSError("nx"),
                                              "stpocnaeastus.blob.core.windows.net": ["10.32.1.5"]}))
    with pytest.raises(RuntimeError):
        nc.ensure_private_path(SETTINGS)


def test_env_override_only_warns(monkeypatch):
    monkeypatch.setenv("REQUIRE_PRIVATE_PATH", "false")
    monkeypatch.setattr(nc, "_resolve", fake({"sql-poc-eastus.database.windows.net": ["20.1.2.3"],
                                              "stpocnaeastus.blob.core.windows.net": ["20.1.2.4"]}))
    nc.ensure_private_path(SETTINGS)


def test_port_in_host_is_ignored():
    s = {**SETTINGS, "source": {"host": "srv.database.windows.net:1433"}}
    assert nc.endpoints(s)[0][1] == "srv.database.windows.net"


def test_direct_mode_checks_only_sql(monkeypatch):
    from src.config import network_check as nc
    s = {"source": {"host": "sql-poc-eastus.database.windows.net"}, "transfer": {"mode": "direct"},
         "network": {"require_private": True}}
    assert [label for label, _ in nc.endpoints(s)] == ["Azure SQL server"]
    monkeypatch.setattr(nc, "_resolve", lambda h: ["10.32.1.4"])
    nc._checked.clear()
    nc.ensure_private_path(s)          # no storage_account needed in direct mode, must not raise


def test_default_mode_is_sts():
    from src.config import network_check as nc
    assert nc.transfer_mode({}) == "sts"
    assert nc.transfer_mode({"transfer": {"mode": "Direct"}}) == "direct"
