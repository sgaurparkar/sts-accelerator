"""Secret names: the <RESOURCE_GROUP>_AZURE_SQL_<SERVER>_<FIELD> layout, the older
AZURE_SQL_<FIELD>_<SUFFIX> layout, typo tolerance, storage secrets and --check-connections."""
import copy
import datetime
from types import SimpleNamespace

import pytest

from src.config import connection as c
from src.config.connection_check import check_targets, sas_expiry
from src.topology.targets import build_migration_targets, settings_for_target

INGREDION = {
    "azure_topology": {"source": "inventory", "inventory": [
        {"resource_group": "ingredion-dev-rg-central-india", "servers": [
            {"name": "ingredion-dev-server-central-indiaa", "location": "Central India", "databases": ["salesdb"]}]},
        {"resource_group": "ingredion-dev-rg-east-us", "servers": [
            {"name": "ingredion-dev-server-east-us", "location": "East US", "databases": ["hrdb"]}]}]},
    "source": {"type": "mssql", "auth": "sql"},
    "azure": {"storage_account": "ingrediondevstg01", "container": "migration-staging"},
    "gcp": {}, "transfer": {"mode": "sts"},
}
IN_USER = "INGREDION_DEV_RG_CENTRAL_INDIA_AZURE_SQL_INGREDION_DEV_SERVER_CENTRAL_INDIAA_USERNAME"
IN_PWD = "INGREDION_DEV_RG_CENTRAL_INDIA_AZURE_SQL_INGREDION_DEV_SERVER_CENTRAL_INDIAA_PASSWORD"
US_USER = "INGREDION_DEV_RG_EAST_US_AZURE_SQL_INGREDION_DEV_SERVER_EAST_US_USERNAME"
US_PWD_TYPO = "INGREDION_DEV_RG_EAST_USAZURE_SQL_INGREDION_DEV_SERVER_EAST_US_PASSWORD"   # missing "_"


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    import os
    for k in list(os.environ):
        if "AZURE_SQL" in k or "AZURE_SAS" in k or "AZURE_STORAGE" in k:
            monkeypatch.delenv(k, raising=False)
    c.configure_secrets({})
    c._WARNED.clear()
    yield
    c.configure_secrets({})


def targets():
    s = copy.deepcopy(INGREDION)
    return s, build_migration_targets(s)


def src_of(i):
    s, ts = targets()
    return settings_for_target(s, ts[i])


def test_resource_group_server_layout(monkeypatch):
    monkeypatch.setenv(IN_USER, "sts_login")
    monkeypatch.setenv(IN_PWD, "p@ss##word1")
    monkeypatch.setenv("AZURE_SQL_PASSWORD", "shared-should-lose")
    src = src_of(0)["source"]
    assert c.sql_secret_candidates("USERNAME", src)[0] == IN_USER
    assert c.sql_secret("USERNAME", src) == ("sts_login", IN_USER)
    assert c.sql_secret("PASSWORD", src) == ("p@ss##word1", IN_PWD)


def test_typo_in_variable_name_is_tolerated_with_warning(monkeypatch, caplog):
    monkeypatch.setenv(US_USER, "sts_login")
    monkeypatch.setenv(US_PWD_TYPO, "pw")
    src = src_of(1)["source"]
    with caplog.at_level("WARNING", logger="sts.secrets"):
        assert c.sql_secret("PASSWORD", src) == ("pw", US_PWD_TYPO)
    assert "differ only in underscores" in caplog.text
    c.configure_secrets({"fuzzy_match": False})
    with pytest.raises(RuntimeError, match="INGREDION_DEV_RG_EAST_US_AZURE_SQL_INGREDION_DEV_SERVER_EAST_US_PASSWORD"):
        c.sql_secret("PASSWORD", src)


def test_older_layouts_and_shared_login_still_work(monkeypatch):
    monkeypatch.setenv("AZURE_SQL_USERNAME", "shared")
    monkeypatch.setenv("AZURE_SQL_PASSWORD_INGREDION_DEV_SERVER_EAST_US", "per-server")
    monkeypatch.setenv("AZURE_SQL_PASSWORD_CENTRALINDIA", "per-location")
    assert c.sql_secret("USERNAME", src_of(1)["source"])[0] == "shared"
    assert c.sql_secret("PASSWORD", src_of(1)["source"])[0] == "per-server"
    assert c.sql_secret("PASSWORD", src_of(0)["source"])[0] == "per-location"


def test_custom_templates(monkeypatch):
    c.configure_secrets({"sql_name_templates": ["SQL_{DATABASE}_{FIELD}"]})
    monkeypatch.setenv("SQL_SALESDB_USERNAME", "u")
    assert c.sql_secret("USERNAME", src_of(0)["source"]) == ("u", "SQL_SALESDB_USERNAME")


def test_build_sql_engine_uses_the_rg_server_login(monkeypatch):
    monkeypatch.setenv(IN_USER, "sts_login")
    monkeypatch.setenv(IN_PWD, "p@ss##word1")
    src = src_of(0)["source"]
    src["host"] = "ingredion-dev-server-central-indiaa.database.windows.net"
    captured = {}
    import sqlalchemy
    monkeypatch.setattr(sqlalchemy, "create_engine", lambda url, **kw: captured.setdefault("url", url))
    c.build_sql_engine(src)
    url = captured["url"]
    assert url.username == "sts_login" and url.password == "p@ss##word1" and url.database == "salesdb"


def test_storage_secrets_shared_and_rg_specific(monkeypatch):
    monkeypatch.setenv("AZURE_SAS_TOKEN", "sv=1&se=2027-04-01T14:38:30Z&sig=x")
    monkeypatch.setenv("INGREDION_DEV_RG_EAST_US_AZURE_SAS_TOKEN", "us-token")
    assert c.storage_secret("AZURE_SAS_TOKEN", src_of(0)) == ("sv=1&se=2027-04-01T14:38:30Z&sig=x", "AZURE_SAS_TOKEN")
    assert c.storage_secret("AZURE_SAS_TOKEN", src_of(1))[0] == "us-token"


def test_sas_expiry():
    assert sas_expiry('?sv=2026-02-06&se=2027-04-01T14:38:30Z&sig=6g%2B') == \
        datetime.datetime(2027, 4, 1, 14, 38, 30, tzinfo=datetime.timezone.utc)
    assert sas_expiry("sv=1") is None


def test_check_connections_reports_variable_names_and_problems(monkeypatch):
    monkeypatch.setenv(IN_USER, "u")
    monkeypatch.setenv(IN_PWD, "p")
    monkeypatch.setenv("AZURE_SAS_TOKEN", "sv=1&se=2020-01-01T00:00:00Z&sig=x")
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING",
                       "DefaultEndpointsProtocol=https;AccountName=ingrediondevstg01;AccountKey=k;EndpointSuffix=x")
    s, ts = targets()
    checks = check_targets(s, ts, connect=False)
    assert checks[0]["vars"]["username"] == IN_USER and checks[0]["vars"]["azure_sas_token"] == "AZURE_SAS_TOKEN"
    assert not checks[0]["ok"] and any("expired" in p for p in checks[0]["problems"])
    assert not checks[1]["ok"] and any("Missing username" in p for p in checks[1]["problems"])


def test_check_connections_probe(monkeypatch):
    monkeypatch.setenv("AZURE_SQL_USERNAME", "u")
    monkeypatch.setenv("AZURE_SQL_PASSWORD", "p")
    s, ts = targets()
    s["transfer"] = {"mode": "direct"}
    row = SimpleNamespace(db="salesdb", edition="SQL Azure", version="12", login_name="u", can_view_definition=0,
                          tables=3, views=2, procedures=5, functions=0, hidden_definitions=0)

    class Conn:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def execute(self, q):
            return SimpleNamespace(fetchone=lambda: row)

    engine = SimpleNamespace(connect=Conn, dispose=lambda: None)
    checks = check_targets(s, ts[:1], engine_factory=lambda cfg: engine)
    assert checks[0]["ok"] and checks[0]["info"]["procedures"] == 5
    assert any("VIEW DEFINITION" in w for w in checks[0]["warnings"])


# ---- Azure Key Vault ---------------------------------------------------------------------------
class FakeVault:
    """Stands in for azure.keyvault.secrets.SecretClient."""

    def __init__(self, secrets, deny=False):
        self.secrets, self.deny, self.gets = secrets, deny, []

    def get_secret(self, name):
        from azure.core.exceptions import HttpResponseError, ResourceNotFoundError
        self.gets.append(name)
        if self.deny:
            err = HttpResponseError(message="Forbidden")
            err.status_code = 403
            raise err
        if name not in self.secrets:
            raise ResourceNotFoundError("not found")
        return SimpleNamespace(value=self.secrets[name])

    def list_properties_of_secrets(self):
        return [SimpleNamespace(name=n, enabled=True) for n in self.secrets]


VAULT = "https://ingredion-kv.vault.azure.net/"


def use_vault(monkeypatch, secrets, deny=False, **cfg):
    vault = FakeVault(secrets, deny)
    monkeypatch.setattr(c, "_kv_client", lambda url: vault)
    c.configure_secrets({"provider": "azure_key_vault", "key_vault_url": VAULT, **cfg})
    return vault


def test_vault_secret_name():
    assert c.vault_secret_name(IN_PWD) == \
        "ingredion-dev-rg-central-india-azure-sql-ingredion-dev-server-central-indiaa-password"


def test_key_vault_supplies_sql_logins_with_exact_names(monkeypatch):
    use_vault(monkeypatch, {c.vault_secret_name(IN_USER): "kv-user", c.vault_secret_name(IN_PWD): "kv-p@ss##1"})
    src = src_of(0)["source"]
    assert c.sql_secret("USERNAME", src) == ("kv-user", f"keyvault:{c.vault_secret_name(IN_USER)}")
    assert c.sql_secret("PASSWORD", src)[0] == "kv-p@ss##1"


def test_key_vault_accepts_a_typo_secret_name(monkeypatch):
    typo = c.vault_secret_name(US_PWD_TYPO)                 # ...-east-usazure-sql-... (missing "-")
    use_vault(monkeypatch, {typo: "us-secret"})
    value, source = c.sql_secret("PASSWORD", src_of(1)["source"])
    assert value == "us-secret" and source == f"keyvault:{typo}"


def test_env_wins_over_key_vault_and_cache(monkeypatch):
    vault = use_vault(monkeypatch, {c.vault_secret_name(IN_USER): "kv-user"})
    monkeypatch.setenv(IN_USER, "local-user")
    assert c.sql_secret("USERNAME", src_of(0)["source"]) == ("local-user", IN_USER)
    assert vault.gets == []                                  # vault never asked
    monkeypatch.delenv(IN_USER)
    c.sql_secret("USERNAME", src_of(0)["source"]); c.sql_secret("USERNAME", src_of(0)["source"])
    assert vault.gets.count(c.vault_secret_name(IN_USER)) == 1   # cached


def test_key_vault_missing_and_denied(monkeypatch):
    use_vault(monkeypatch, {})
    with pytest.raises(RuntimeError, match="Key Vault secret ingredion-dev-rg-central-india-azure-sql"):
        c.sql_secret("PASSWORD", src_of(0)["source"])
    use_vault(monkeypatch, {}, deny=True)
    with pytest.raises(RuntimeError, match="Key Vault Secrets User"):
        c.sql_secret("PASSWORD", src_of(0)["source"])


def test_key_vault_url_from_env_and_required(monkeypatch):
    c.configure_secrets({})
    monkeypatch.delenv("KEY_VAULT_URL", raising=False)
    assert c.key_vault_url() is None
    monkeypatch.setenv("KEY_VAULT_URL", "ingredion-kv")
    assert c.key_vault_url() == VAULT
    monkeypatch.delenv("KEY_VAULT_URL")
    c.configure_secrets({"provider": "azure_key_vault"})
    with pytest.raises(RuntimeError, match="key_vault_url"):
        c.key_vault_url()


def test_check_connections_shows_key_vault_source(monkeypatch):
    use_vault(monkeypatch, {c.vault_secret_name(IN_USER): "u", c.vault_secret_name(IN_PWD): "p",
                            "azure-sas-token": "sv=1&se=2030-01-01T00:00:00Z&sig=x",
                            "azure-storage-connection-string": "AccountName=ingrediondevstg01;AccountKey=k"})
    s, ts = targets()
    checks = check_targets(s, ts[:1], connect=False)
    assert checks[0]["vars"]["password"].startswith("keyvault:") and checks[0]["ok"]