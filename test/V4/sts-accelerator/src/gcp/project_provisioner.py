"""
project_provisioner.py

Creates (or verifies) one GCP project per Azure Resource Group, so the
`ResourceGroup1 -> project resourcegroup1` mapping is done in code instead
of by hand:

  1. project   get it; if missing and gcp.projects.create_missing is true,
               create it under gcp.projects.parent (organizations/<id> or
               folders/<id>) with the RG name as display name and labels
               recording where it came from
  2. billing   link gcp.projects.billing_account if the project has none
               (BigQuery, GCS and Storage Transfer all need billing)
  3. APIs      enable bigquery, storage and storagetransfer (and anything in
               gcp.projects.enable_apis)
  4. ready     wait until BigQuery answers in the new project (a fresh
               project/API can take a minute to propagate)

Identity needs: resourcemanager.projects.create on the parent,
billing.resourceAssociations.create on the billing account,
serviceusage.services.enable on the project. With create_missing: false the
projects must already exist and only steps 2-4 run.

Requires: google-cloud-resource-manager, google-cloud-billing,
google-cloud-service-usage
"""
import re
import time

from google.api_core import exceptions as gexc

from src.common import log

LOG = log.get("provisioner")

_DEFAULT_APIS = ["bigquery.googleapis.com", "storage.googleapis.com", "storagetransfer.googleapis.com"]
_ENSURED: set[str] = set()     # projects already provisioned in this process


class ProvisioningError(RuntimeError):
    """A GCP project could not be created or prepared."""


def _label(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "-", str(value).lower()).strip("-")[:63]


class ProjectProvisioner:
    def __init__(self, settings: dict, projects_client=None, billing_client=None,
                 usage_client=None, bq_client_factory=None, sleep=time.sleep):
        self.cfg = ((settings.get("gcp") or {}).get("projects") or {})
        self.create_missing = bool(self.cfg.get("create_missing", True))
        self.parent = self.cfg.get("parent") or None
        self.billing_account = self.cfg.get("billing_account") or None
        if self.billing_account and not str(self.billing_account).startswith("billingAccounts/"):
            self.billing_account = f"billingAccounts/{self.billing_account}"
        self.require_billing = bool(self.cfg.get("require_billing", True))
        self.apis = list(dict.fromkeys(_DEFAULT_APIS + list(self.cfg.get("enable_apis") or [])))
        self.extra_labels = {_label(k): _label(v) for k, v in (self.cfg.get("labels") or {}).items()}
        self.ready_timeout = int(self.cfg.get("api_ready_timeout_seconds", 240))
        self._projects = projects_client
        self._billing = billing_client
        self._usage = usage_client
        self._bq_factory = bq_client_factory
        self._sleep = sleep

    # ------------------------------------------------------------- clients --
    def _projects_client(self):
        if self._projects is None:
            from google.cloud import resourcemanager_v3
            self._projects = resourcemanager_v3.ProjectsClient()
        return self._projects

    def _billing_client(self):
        if self._billing is None:
            from google.cloud import billing_v1
            self._billing = billing_v1.CloudBillingClient()
        return self._billing

    def _usage_client(self):
        if self._usage is None:
            from google.cloud import service_usage_v1
            self._usage = service_usage_v1.ServiceUsageClient()
        return self._usage

    def _bq_client(self, project_id: str):
        if self._bq_factory:
            return self._bq_factory(project_id)
        from google.cloud import bigquery
        return bigquery.Client(project=project_id)

    # ----------------------------------------------------------------- API --
    def ensure_project(self, project_id: str, display_name: str, resource_group: str) -> dict:
        if project_id in _ENSURED:
            return {"project_id": project_id, "created": False, "cached": True}
        LOG.info(f"Ensuring GCP project {project_id} (Azure RG {resource_group}) ...")
        created = self._ensure_exists(project_id, display_name, resource_group)
        self._ensure_billing(project_id)
        self._enable_apis(project_id)
        self._wait_until_ready(project_id)
        _ENSURED.add(project_id)
        LOG.info(f"Project ready: {project_id}" + (" (created)" if created else ""))
        return {"project_id": project_id, "created": created, "cached": False}

    def _get(self, project_id: str):
        try:
            return self._projects_client().get_project(name=f"projects/{project_id}")
        except (gexc.NotFound, gexc.PermissionDenied, gexc.Forbidden):
            # GCP answers 403 for "doesn't exist" too, so both mean "not usable by us yet"
            return None

    def _ensure_exists(self, project_id: str, display_name: str, resource_group: str) -> bool:
        project = self._get(project_id)
        if project is not None:
            state = getattr(getattr(project, "state", None), "name", str(getattr(project, "state", "")))
            if "DELETE" in state.upper():
                raise ProvisioningError(f"Project {project_id} is pending deletion — undelete it in the console "
                                        "or map the resource group to another id (gcp.projects.id_overrides).")
            return False
        if not self.create_missing:
            raise ProvisioningError(f"Project {project_id} does not exist (or this identity can't see it) and "
                                    "gcp.projects.create_missing is false — create it, or grant access.")

        from google.cloud import resourcemanager_v3
        labels = {"migrated_from": "azure", "azure_resource_group": _label(resource_group), **self.extra_labels}
        body = resourcemanager_v3.Project(project_id=project_id, display_name=display_name, labels=labels)
        if self.parent:
            body.parent = self.parent
        LOG.info(f"Creating project {project_id} "
              f"under {self.parent or '(no parent — account default)'} ...")
        try:
            self._projects_client().create_project(project=body).result(timeout=600)
        except gexc.AlreadyExists as e:
            raise ProvisioningError(
                f'Project id "{project_id}" is already taken (project ids are global across all of GCP). '
                "Set gcp.projects.id_prefix (e.g. your company/env) or gcp.projects.id_overrides.") from e
        except (gexc.PermissionDenied, gexc.Forbidden) as e:
            raise ProvisioningError(f"No permission to create project {project_id} under "
                                    f"{self.parent or 'the default parent'}: {e.message if hasattr(e, 'message') else e}") from e
        return True

    def _ensure_billing(self, project_id: str) -> None:
        name = f"projects/{project_id}"
        try:
            info = self._billing_client().get_project_billing_info(name=name)
        except (gexc.PermissionDenied, gexc.Forbidden) as e:
            LOG.warning(f"cannot read billing for {project_id} ({e}); assuming it is set.")
            return
        if info.billing_enabled and (not self.billing_account or info.billing_account_name == self.billing_account):
            return
        if info.billing_enabled and self.billing_account:
            LOG.info(f"{project_id} already bills to {info.billing_account_name}; "
                  f"leaving it (configured: {self.billing_account}).")
            return
        if not self.billing_account:
            msg = (f"Project {project_id} has no billing account. BigQuery loads, GCS buckets and Storage "
                   "Transfer need billing — set gcp.projects.billing_account in config/settings.yaml.")
            if self.require_billing:
                raise ProvisioningError(msg)
            LOG.warning(f"{msg}")
            return
        from google.cloud import billing_v1
        LOG.info(f"Linking {project_id} to {self.billing_account} ...")
        try:
            self._billing_client().update_project_billing_info(
                name=name, project_billing_info=billing_v1.ProjectBillingInfo(billing_account_name=self.billing_account))
        except (gexc.PermissionDenied, gexc.Forbidden) as e:
            raise ProvisioningError(f"No permission to link {project_id} to {self.billing_account} "
                                    "(needs Billing Account User on it).") from e

    def _enable_apis(self, project_id: str) -> None:
        LOG.info(f"Enabling APIs on {project_id}: {', '.join(self.apis)} ...")
        for i in range(0, len(self.apis), 20):          # API limit: 20 per batch
            try:
                op = self._usage_client().batch_enable_services(
                    request={"parent": f"projects/{project_id}", "service_ids": self.apis[i:i + 20]})
                op.result(timeout=900)
            except (gexc.PermissionDenied, gexc.Forbidden) as e:
                raise ProvisioningError(f"No permission to enable APIs on {project_id} "
                                        "(needs serviceusage.services.enable).") from e

    def _wait_until_ready(self, project_id: str) -> None:
        deadline = time.monotonic() + self.ready_timeout
        delay = 5
        while True:
            try:
                list(self._bq_client(project_id).list_datasets(max_results=1))
                return
            except (gexc.PermissionDenied, gexc.Forbidden, gexc.NotFound, gexc.ServiceUnavailable) as e:
                if time.monotonic() >= deadline:
                    raise ProvisioningError(f"BigQuery is still not usable in {project_id} after "
                                            f"{self.ready_timeout}s: {e}") from e
                LOG.info(f"BigQuery not ready in {project_id} yet, retrying in {delay}s ...")
                self._sleep(delay)
                delay = min(delay * 2, 30)
