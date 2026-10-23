"""
project_manager.py

Multi-project mode: makes sure the GCP project for an Azure resource group exists, is billed,
has the BigQuery and Storage APIs on, and that the runner service account can use it.
Every step is idempotent, so running it for a project that is already set up changes nothing.

It calls the `gcloud` CLI (present on the runner VM, authenticated as sts-runner-sa), so no extra
Python packages are needed.

Access model (see MULTI_PROJECT.md):
  * multi_project.folder_id set  -> projects are created INSIDE that folder. The runner's roles are
    granted ONCE on the folder (scripts/gcp_multi_project_setup.sh) and every new project inherits
    them - nothing is granted per project here.
  * no folder_id                 -> after creating a project this module grants the runner the roles
    in multi_project.grant_roles on that project (needs roles/resourcemanager.projectIamAdmin
    for the creator, or being the project's owner, which a creator is by default).
"""
import subprocess
import time

REQUIRED_APIS = ["bigquery.googleapis.com", "storage.googleapis.com"]
DEFAULT_ROLES = ["roles/bigquery.jobUser", "roles/bigquery.dataEditor", "roles/storage.admin"]

_ENSURED: set[str] = set()


class ProjectError(RuntimeError):
    """A project could not be created / prepared. Message says what to fix."""


def _gcloud(args: list) -> tuple[int, str, str]:
    p = subprocess.run(["gcloud", *args], capture_output=True, text=True)
    return p.returncode, (p.stdout or "").strip(), (p.stderr or "").strip()


def _wait_for_access(project_id: str, tries: int = 12, delay: float = 10.0, sleep=time.sleep) -> None:
    """IAM inherited from a folder can take a minute or two to apply to a new project."""
    from google.api_core import exceptions as gexc
    from google.cloud import bigquery
    last = None
    for _ in range(tries):
        try:
            list(bigquery.Client(project=project_id).list_datasets(max_results=1))
            return
        except (gexc.Forbidden, gexc.PermissionDenied, gexc.NotFound) as e:
            last = e
            sleep(delay)
    raise ProjectError(f"The runner still has no BigQuery access to {project_id} after waiting "
                       f"({last}). Check the roles on the folder / project (MULTI_PROJECT.md, step 2).")


def ensure_project(settings: dict, run=_gcloud, wait_access=_wait_for_access) -> str:
    """Creates / prepares settings['gcp']['project_id'] when multi_project is enabled.
    Returns the project id. No-op (returns the id) when multi_project is off."""
    gcp = settings["gcp"]
    project_id = gcp["project_id"]
    mp = settings.get("multi_project") or {}
    if not mp.get("enabled") or project_id in _ENSURED:
        return project_id

    folder = str(mp.get("folder_id") or "").strip()
    billing = str(mp.get("billing_account") or "").strip()
    runner_sa = str(mp.get("runner_service_account") or "").strip()
    if not billing:
        raise ProjectError("multi_project.billing_account is not set in config/settings.yaml "
                           "(e.g. 012345-ABCDEF-678901; list yours with `gcloud billing accounts list`).")
    if not folder and not runner_sa:
        raise ProjectError("Without multi_project.folder_id the runner must be granted access per project, "
                           "so set multi_project.runner_service_account (the sts-runner-sa e-mail).")
    rg = (settings.get("source") or {}).get("resource_group") or ""

    rc, out, _ = run(["projects", "describe", project_id, "--format=value(lifecycleState)"])
    if rc == 0:
        if out and out != "ACTIVE":
            raise ProjectError(f"Project {project_id} is in state {out}; restore or pick another id "
                               "(multi_project.projects in config/settings.yaml).")
        print(f"[project_manager] Project exists: {project_id}")
        created = False
    else:
        print(f"[project_manager] Creating project {project_id} "
              f"({'in folder ' + folder if folder else 'no folder'}) for resource group '{rg}' ...")
        labels = "managed-by=sts-accelerator" + (f",resource-group={_label(rg)}" if rg else "")
        args = ["projects", "create", project_id, f"--labels={labels}", "--quiet"]
        if folder:
            args.append(f"--folder={folder}")
        rc, _, err = run(args)
        if rc != 0:
            low = err.lower()
            if "already exists" in low or "already_exists" in low:
                raise ProjectError(f"Project id {project_id} is already taken by a project this account cannot "
                                   "see (ids are global). Set multi_project.projects."
                                   f'"{rg}" to a different id.')
            if "permission" in low or "denied" in low:
                raise ProjectError(f"Not allowed to create {project_id}: the runner needs "
                                   "roles/resourcemanager.projectCreator on the folder "
                                   f"(MULTI_PROJECT.md step 2). gcloud said: {err}")
            raise ProjectError(f"Could not create project {project_id}: {err}")
        created = True

    rc, out, _ = run(["billing", "projects", "describe", project_id, "--format=value(billingEnabled)"])
    if rc != 0 or out.lower() != "true":
        rc, _, err = run(["billing", "projects", "link", project_id, f"--billing-account={billing}"])
        if rc != 0:
            raise ProjectError(f"Could not link billing account {billing} to {project_id}: {err}. "
                               "The runner needs roles/billing.user on the billing account.")
        print(f"[project_manager] Billing linked: {project_id} -> {billing}")

    rc, _, err = run(["services", "enable", *REQUIRED_APIS, f"--project={project_id}"])
    if rc != 0:
        raise ProjectError(f"Could not enable APIs on {project_id}: {err} "
                           "(needs roles/serviceusage.serviceUsageAdmin).")

    if not folder:                     # no folder to inherit from: grant per project
        for role in (mp.get("grant_roles") or DEFAULT_ROLES):
            role = role if str(role).startswith("roles/") else f"roles/{role}"
            rc, _, err = run(["projects", "add-iam-policy-binding", project_id,
                              f"--member=serviceAccount:{runner_sa}", f"--role={role}", "--quiet"])
            if rc != 0:
                raise ProjectError(f"Could not grant {role} on {project_id} to {runner_sa}: {err}")
        print(f"[project_manager] Granted {runner_sa} access on {project_id}")

    if created or folder:
        wait_access(project_id)
    _ENSURED.add(project_id)
    return project_id


def _label(text: str) -> str:
    import re
    return re.sub(r"[^a-z0-9_-]+", "-", str(text).lower()).strip("-")[:60] or "none"
