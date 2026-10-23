"""
targets.py

Turns the discovered Azure hierarchy into migration targets and gives every
other module a per-target settings dict, so none of them need to know how
the hierarchy was mapped:

    build_migration_targets(settings, filters)  -> [MigrationTarget, ...]
    settings_for_target(settings, target)       -> deep copy of settings with
        source.host / database / server / resource_group / secret_suffixes
        gcp.project_id / location / dataset / control_dataset / gcs_bucket / object_prefix
        azure.storage_account (per Azure location, if configured)
        target  (the MigrationTarget as a dict)

Validation done up front, before anything is created in GCP:
  * two resource groups (e.g. same name in two subscriptions) must not map to
    the same project id
  * two databases must not map to the same dataset id inside one project
  * every Azure location must map to a GCP location
"""
import copy

from src.common import log

from src.topology.azure_discovery import AzureTopologyDiscovery, TopologyError
from src.topology.location_mapping import LocationMappingError, resolve_gcp_location
from src.topology.models import AzureSqlDatabase, MigrationTarget
from src.topology.naming import (
    NamingError,
    control_dataset_id,
    dataset_id_for,
    landing_bucket_name,
    object_prefix,
    project_display_name,
    project_id_for_resource_group,
    secret_suffixes,
    slug,
)

LOG = log.get("targets")


def target_for_database(db: AzureSqlDatabase, settings: dict) -> MigrationTarget:
    gcp_cfg = settings.get("gcp") or {}
    project_id = project_id_for_resource_group(db.resource_group, gcp_cfg)
    location = resolve_gcp_location(db.azure_location, settings)
    return MigrationTarget(
        source=db,
        gcp_project_id=project_id,
        gcp_project_display_name=project_display_name(db.resource_group),
        gcp_location=location,
        dataset_id=dataset_id_for(db.server, db.database, gcp_cfg, resource_group=db.resource_group),
        control_dataset_id=control_dataset_id(location, gcp_cfg),
        landing_bucket=landing_bucket_name(project_id, location, gcp_cfg),
        object_prefix=object_prefix(db.resource_group, db.server, db.database),
    )


def build_migration_targets(settings: dict, filters: dict | None = None,
                            databases: list[AzureSqlDatabase] | None = None,
                            strict: bool = False, problems_out: list | None = None) -> list[MigrationTarget]:
    """Discover (or take) the Azure databases in scope and map each one to GCP.

    A database that can't be mapped (unknown Azure region, invalid name) is
    reported and left out; the others still run. strict=True raises instead.
    Name clashes between databases always raise: guessing would mix data.
    """
    if databases is None:
        databases = AzureTopologyDiscovery(settings).discover(filters)
    targets, problems = [], []
    for db in databases:
        try:
            targets.append(target_for_database(db, settings))
        except (NamingError, LocationMappingError) as e:
            problems.append(f"{db.key}: {e}")
    if problems:
        msg = "Cannot map these Azure databases to GCP:\n  " + "\n  ".join(problems)
        if strict or not targets:
            raise TopologyError(msg)
        LOG.warning("%s\n(they are skipped; the other %d database(s) continue)", msg, len(targets))
        if problems_out is not None:
            problems_out.extend(problems)
    _validate_unique(targets)
    return targets


def _validate_unique(targets: list[MigrationTarget]) -> None:
    rg_for_project: dict[str, tuple] = {}
    ds_owner: dict[tuple, str] = {}
    errors = []
    for t in targets:
        rg_identity = (t.source.subscription_id or "", t.source.resource_group.lower())
        seen = rg_for_project.setdefault(t.gcp_project_id, rg_identity)
        if seen != rg_identity:
            errors.append(f'Resource groups "{seen[1]}" (subscription {seen[0] or "?"}) and '
                          f'"{t.source.resource_group}" (subscription {t.source.subscription_id or "?"}) both map '
                          f'to project "{t.gcp_project_id}" — set gcp.projects.id_overrides for one of them.')
        owner = ds_owner.setdefault((t.gcp_project_id, t.dataset_id), t.key)
        if owner != t.key:
            errors.append(f'{owner} and {t.key} both map to dataset {t.gcp_project_id}.{t.dataset_id} — '
                          "set gcp.datasets.overrides for one of them.")
    if errors:
        raise TopologyError("Azure -> GCP name clash:\n  " + "\n  ".join(sorted(set(errors))))


def projects_of(targets: list[MigrationTarget]) -> dict[str, MigrationTarget]:
    """project id -> first target in it (carries the RG display name)."""
    out: dict[str, MigrationTarget] = {}
    for t in targets:
        out.setdefault(t.gcp_project_id, t)
    return out


def _storage_account_for(settings: dict, db: AzureSqlDatabase) -> str | None:
    azure = settings.get("azure") or {}
    by_loc = {slug(k, ""): v for k, v in (azure.get("storage_accounts_by_location") or {}).items() if v}
    return by_loc.get(db.azure_location) or azure.get("storage_account")


def settings_for_target(settings: dict, target: MigrationTarget) -> dict:
    """Deep copy of settings resolved for ONE database (never mutates the input)."""
    if (settings.get("target") or {}).get("source"):
        already = MigrationTarget.from_dict(settings["target"])
        if already.key == target.key:
            return settings
        raise TopologyError(f"Settings are already resolved for {already.key}, not {target.key}.")

    for section in ("source", "gcp", "azure"):
        if not isinstance(settings.get(section), dict):
            raise TopologyError(f"config/settings.yaml is missing the `{section}:` section.")

    db = target.source
    resolved = copy.deepcopy(settings)
    src = resolved["source"]
    src.update({
        "host": db.server_fqdn,
        "database": db.database,
        "server": db.server,
        "resource_group": db.resource_group,
        "azure_location": db.azure_location,
        "secret_suffixes": secret_suffixes(db.server, db.azure_location, db.resource_group),
        "target_key": target.key,
    })
    gcp = resolved["gcp"]
    gcp.update({
        "project_id": target.gcp_project_id,
        "location": target.gcp_location,
        "dataset": target.dataset_id,
        "control_dataset": target.control_dataset_id,
        "gcs_bucket": target.landing_bucket,
        "object_prefix": target.object_prefix,
    })
    account = _storage_account_for(settings, db)
    if account:
        resolved["azure"]["storage_account"] = account
    resolved["target"] = target.to_dict()
    return resolved


def format_topology(targets: list[MigrationTarget]) -> str:
    """Human-readable Azure -> GCP mapping, grouped like the Azure tree."""
    if not targets:
        return "(no Azure SQL databases in scope)"
    lines = []
    by_rg: dict[str, list[MigrationTarget]] = {}
    for t in targets:
        by_rg.setdefault(t.source.resource_group, []).append(t)
    for rg, items in by_rg.items():
        lines.append(f"Azure RG {rg}  ->  GCP project {items[0].gcp_project_id}")
        for t in items:
            s = t.source
            lines.append(f"  {s.server}/{s.database}  [{s.azure_location}]")
            lines.append(f"      -> dataset {t.gcp_project_id}.{t.dataset_id}  [{t.gcp_location}]")
            lines.append(f"         bucket gs://{t.landing_bucket}/parquet/{t.object_prefix}/  "
                         f"control {t.control_dataset_id}")
    return "\n".join(lines)
