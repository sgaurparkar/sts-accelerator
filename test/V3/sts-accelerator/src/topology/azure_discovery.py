"""
azure_discovery.py

Finds every Azure SQL database in scope — resource group, server, database
and the server's Azure location — so nothing about the Azure hierarchy is
hand-typed in the pipeline.

Two sources (azure_topology.source in config/settings.yaml):

  arm        (default) live from Azure Resource Manager: every logical SQL
             server in the subscription(s), its resource group (parsed from
             the resource id), its location, and every database on it. Needs
             azure-identity + azure-mgmt-sql and Reader access on the
             subscription. Credentials come from DefaultAzureCredential
             (service principal env vars AZURE_CLIENT_ID / AZURE_TENANT_ID /
             AZURE_CLIENT_SECRET, managed identity, or `az login`).
  inventory  the azure_topology.inventory list in settings.yaml — for
             environments where the migration identity has no ARM access.

Scope filters (settings + CLI, case-insensitive, shell wildcards allowed):
  resource_groups / servers / databases / azure_locations   include lists (empty = all)
  exclude_resource_groups / exclude_servers / exclude_databases
The system database `master` is always excluded.
"""
import fnmatch
import os
import re

from src.common import log

from src.topology.location_mapping import normalize_azure_location
from src.topology.models import AzureSqlDatabase
from src.topology.naming import short_server_name


class TopologyError(RuntimeError):
    """Azure topology could not be discovered or is misconfigured."""


_SYSTEM_DATABASES = {"master"}
_DEFAULT_SKIP_STATUSES = {"offline", "disabled", "creating", "dropping", "inaccessible", "restoring"}
LOG = log.get("azure_discovery")


def _is_secondary(db) -> bool:
    """Geo / named / standby replicas hold a copy of another database's data."""
    if getattr(db, "secondary_type", None):
        return True
    kind = str(getattr(db, "kind", "") or "").lower()
    return "secondary" in kind


def _is_datawarehouse(db) -> bool:
    kind = str(getattr(db, "kind", "") or "").lower()
    tier = str(getattr(getattr(db, "sku", None), "tier", "") or "").lower()
    return "datawarehouse" in kind or tier == "datawarehouse"


def _is_system(db) -> bool:
    return "system" in str(getattr(db, "kind", "") or "").lower().split(",")


_RG_IN_ID = re.compile(r"/resourcegroups/([^/]+)/", re.I)
_SUB_IN_ID = re.compile(r"/subscriptions/([^/]+)/", re.I)


def _as_list(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    return [str(v).strip() for v in value if str(v).strip()]


def _matches(value: str, patterns: list[str]) -> bool:
    v = str(value).lower()
    return any(fnmatch.fnmatchcase(v, p.lower()) for p in patterns)


def _loc_matches(location: str, patterns: list[str]) -> bool:
    """'Central India', 'central-india', 'central*' all match 'centralindia'."""
    loc = normalize_azure_location(location)
    return any(fnmatch.fnmatchcase(loc, re.sub(r"[^a-z0-9*?\[\]]", "", p.lower())) for p in patterns)


def filter_databases(dbs: list[AzureSqlDatabase], cli: dict | None, cfg: dict | None = None) -> list[AzureSqlDatabase]:
    """Settings scope (cfg = azure_topology) narrowed by CLI/job filters (cli)."""
    cli, cfg = cli or {}, cfg or {}

    def pick(name):        # CLI filters narrow further than settings, never widen
        return _as_list(cli.get(name)) or _as_list(cfg.get(name))

    inc_rg, inc_srv, inc_db, inc_loc = (pick("resource_groups"), pick("servers"),
                                        pick("databases"), pick("azure_locations"))
    exc_rg = _as_list(cfg.get("exclude_resource_groups"))
    exc_srv = _as_list(cfg.get("exclude_servers"))
    exc_db = _as_list(cfg.get("exclude_databases"))

    out = []
    for d in dbs:
        if d.database.lower() in _SYSTEM_DATABASES:
            continue
        if inc_rg and not _matches(d.resource_group, inc_rg):
            continue
        if inc_srv and not (_matches(d.server, inc_srv) or _matches(d.server_fqdn, inc_srv)):
            continue
        if inc_db and not (_matches(d.database, inc_db) or _matches(f"{d.server}/{d.database}", inc_db)):
            continue
        if inc_loc and not _loc_matches(d.azure_location, inc_loc):
            continue
        if exc_rg and _matches(d.resource_group, exc_rg):
            continue
        if exc_srv and _matches(d.server, exc_srv):
            continue
        if exc_db and (_matches(d.database, exc_db) or _matches(f"{d.server}/{d.database}", exc_db)):
            continue
        out.append(d)
    return out


class AzureTopologyDiscovery:
    def __init__(self, settings: dict, credential=None, sql_client_factory=None):
        self.cfg = settings.get("azure_topology") or {}
        self.source_cfg = settings.get("source") or {}
        self._credential = credential
        self._sql_client_factory = sql_client_factory   # injectable for tests

    # ------------------------------------------------------------------ API --
    def discover(self, filters: dict | None = None) -> list[AzureSqlDatabase]:
        mode = str(self.cfg.get("source") or "arm").lower()
        if mode == "arm":
            found = self._from_arm()
        elif mode == "inventory":
            found = self._from_inventory()
        else:
            raise TopologyError(f'azure_topology.source "{mode}" is not valid — use arm or inventory.')
        scoped = self._apply_filters(found, filters or {})
        scoped.sort(key=lambda d: (d.resource_group.lower(), d.server.lower(), d.database.lower()))
        LOG.info("%d database(s) found via %s, %d in scope", len(found), mode, len(scoped))
        return scoped

    # --------------------------------------------------------------- filters --
    def _apply_filters(self, dbs: list[AzureSqlDatabase], cli: dict) -> list[AzureSqlDatabase]:
        return filter_databases(dbs, cli, self.cfg)

    # ------------------------------------------------------------- inventory --
    def _host_for(self, server: str, explicit_host: str | None = None) -> str:
        if explicit_host:
            return str(explicit_host).strip()
        template = self.source_cfg.get("host_template") or "{server}.database.windows.net"
        return template.format(server=server)

    def _from_inventory(self) -> list[AzureSqlDatabase]:
        inventory = self.cfg.get("inventory")
        if not isinstance(inventory, list) or not inventory:
            raise TopologyError("azure_topology.source is 'inventory' but azure_topology.inventory is empty.")
        out = []
        for rg_entry in inventory:
            rg = (rg_entry or {}).get("resource_group")
            if not rg:
                raise TopologyError("Every azure_topology.inventory entry needs a resource_group.")
            for srv in rg_entry.get("servers") or []:
                name = short_server_name(srv.get("name") or srv.get("host") or "")
                loc = normalize_azure_location(srv.get("location"))
                if not name:
                    raise TopologyError(f'A server under resource group "{rg}" has no name.')
                if not loc:
                    raise TopologyError(f'Server "{name}" in "{rg}" has no location (e.g. "Central India").')
                dbs = _as_list(srv.get("databases"))
                if not dbs:
                    raise TopologyError(f'Server "{name}" in "{rg}" lists no databases.')
                for db in dbs:
                    out.append(AzureSqlDatabase(
                        resource_group=str(rg), server=name,
                        server_fqdn=self._host_for(name, srv.get("host")),
                        database=db, azure_location=loc,
                        subscription_id=rg_entry.get("subscription_id")))
        return out

    # ------------------------------------------------------------------- ARM --
    def _subscriptions(self) -> list[str]:
        subs = _as_list(self.cfg.get("subscription_ids")) or _as_list(os.environ.get("AZURE_SUBSCRIPTION_ID"))
        if not subs:
            raise TopologyError("No Azure subscription to scan — set azure_topology.subscription_ids in "
                                "config/settings.yaml or AZURE_SUBSCRIPTION_ID in .env.")
        return subs

    def _sql_client(self, subscription_id: str):
        if self._sql_client_factory:
            return self._sql_client_factory(subscription_id)
        try:
            from azure.identity import DefaultAzureCredential
            from azure.mgmt.sql import SqlManagementClient
        except ImportError as e:
            raise TopologyError("azure_topology.source 'arm' needs azure-identity and azure-mgmt-sql "
                                "(pip install -r requirements.txt).") from e
        if self._credential is None:
            self._credential = DefaultAzureCredential(exclude_interactive_browser_credential=True)
        return SqlManagementClient(self._credential, subscription_id)

    def _from_arm(self) -> list[AzureSqlDatabase]:
        skip_status = {s.lower() for s in _as_list(self.cfg.get("skip_database_statuses"))} or _DEFAULT_SKIP_STATUSES
        include_secondaries = bool(self.cfg.get("include_secondaries", False))
        include_dw = bool(self.cfg.get("include_datawarehouse", False))
        out = []
        for sub in self._subscriptions():
            client = self._sql_client(sub)
            try:
                servers = list(client.servers.list())
            except Exception as e:
                raise TopologyError(f"Could not list Azure SQL servers in subscription {sub}: {e}") from e
            for server in servers:
                rid = server.id or ""
                m = _RG_IN_ID.search(rid)
                if not m:
                    LOG.warning("Cannot read resource group from id %r — skipped", rid)
                    continue
                rg = m.group(1)
                sub_id = (_SUB_IN_ID.search(rid).group(1) if _SUB_IN_ID.search(rid) else sub)
                loc = normalize_azure_location(server.location)
                fqdn = server.fully_qualified_domain_name or self._host_for(server.name)
                for db in client.databases.list_by_server(rg, server.name):
                    status = getattr(db, "status", None)
                    if status and str(status).lower() in skip_status:
                        LOG.info("Skipping %s/%s/%s: status %s", rg, server.name, db.name, status)
                        continue
                    if _is_system(db):
                        continue
                    if _is_secondary(db) and not include_secondaries:
                        LOG.info("Skipping %s/%s/%s: geo/named replica (its primary is migrated instead)",
                                 rg, server.name, db.name)
                        continue
                    if _is_datawarehouse(db) and not include_dw:
                        LOG.info("Skipping %s/%s/%s: dedicated SQL pool (DataWarehouse)", rg, server.name, db.name)
                        continue
                    out.append(AzureSqlDatabase(
                        resource_group=rg, server=server.name, server_fqdn=fqdn, database=db.name,
                        azure_location=normalize_azure_location(getattr(db, "location", None)) or loc,
                        subscription_id=sub_id, status=str(status) if status else None))
        return out
