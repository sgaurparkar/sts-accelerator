"""
gcs_manager.py — the landing bucket of one (project, location) pair.

  bucket   <project_id>-landing-<location> in the resource group's project, in the
           same GCP location as the dataset (BigQuery can't load across locations)
  objects  parquet/<rg>/<server>/<db>/<schema>/<table>/<scope>/<run>/<file>  (file names: topology/paths.py)

On creation the bucket gets uniform access, labels and a lifecycle rule that
deletes landed Parquet after gcp.landing_bucket.retention_days (default 7), so
landing files never pile up. The project's Storage Transfer agent is granted
access whenever the bucket is first verified in a process (idempotent), so a
pre-existing bucket works too.
"""
import time

from google.api_core import exceptions as gexc
from google.cloud import storage

from src.common import log
from src.topology.naming import slug

LOG = log.get("gcs")
_ENSURED_BUCKETS: set[str] = set()
_STS_ROLES = ("roles/storage.legacyBucketWriter", "roles/storage.objectViewer", "roles/storage.legacyBucketReader")


class GcsManager:
    def __init__(self, config: dict, client=None, sts_client=None):
        self.settings = config
        self.project_id = config["gcp"]["project_id"]
        self.bucket_name = config["gcp"]["gcs_bucket"]
        self.location = config["gcp"].get("location")
        bcfg = config["gcp"].get("landing_bucket") or {}
        self.grant_sts = bool(bcfg.get("grant_sts_access", True))
        self.retention_days = bcfg.get("retention_days", 7)
        self.client = client or storage.Client(project=self.project_id)
        self._sts_client = sts_client

    # ---------------------------------------------------------------- bucket --
    def ensure_bucket(self) -> None:
        if self.bucket_name in _ENSURED_BUCKETS:
            return
        bucket = self._lookup()
        if bucket is None:
            LOG.info("Creating bucket gs://%s in %s (project %s)", self.bucket_name, self.location, self.project_id)
            try:
                new = self.client.bucket(self.bucket_name)
                new.iam_configuration.uniform_bucket_level_access_enabled = True
                src = self.settings.get("source") or {}
                new.labels = {"managed_by": "sts-accelerator", "purpose": "migration-landing",
                              "azure_resource_group": slug(src.get("resource_group"), "-")[:63] or "unknown"}
                if self.retention_days:
                    new.lifecycle_rules = [{"action": {"type": "Delete"},
                                            "condition": {"age": int(self.retention_days),
                                                          "matchesPrefix": ["parquet/"]}}]
                bucket = self.client.create_bucket(new, project=self.project_id, location=self.location)
            except gexc.Conflict:
                bucket = self._lookup()
                if bucket is None:
                    raise RuntimeError(f"Bucket name gs://{self.bucket_name} is not available. Change "
                                       "gcp.landing_bucket.name_template to a unique pattern.")
            except gexc.Forbidden as e:
                raise RuntimeError(f"No permission to create gs://{self.bucket_name} in {self.project_id} "
                                   "(needs storage.buckets.create on the project).") from e
        existing = (getattr(bucket, "location", None) or "").lower()
        if self.location and existing and existing != self.location.lower():
            raise RuntimeError(
                f"gs://{self.bucket_name} already exists in {existing}, but this database maps to "
                f"{self.location}. BigQuery needs the bucket and dataset in the same location — change "
                "gcp.landing_bucket.name_template or gcp.location_mapping.")
        transfer_mode = str(((self.settings.get("transfer") or {}).get("mode")) or "sts").lower()
        if self.grant_sts and transfer_mode == "sts":
            self._grant_sts_access(bucket)
        _ENSURED_BUCKETS.add(self.bucket_name)

    def _lookup(self):
        try:
            return self.client.lookup_bucket(self.bucket_name)
        except gexc.Forbidden as e:
            raise RuntimeError(f"Cannot access gs://{self.bucket_name}: the name may belong to another "
                               "project, or this account lacks storage.buckets.get.") from e

    def _sts_agent_email(self) -> str:
        if self._sts_client is None:
            from google.cloud import storage_transfer_v1
            self._sts_client = storage_transfer_v1.StorageTransferServiceClient()
        return self._sts_client.get_google_service_account(request={"project_id": self.project_id}).account_email

    def _grant_sts_access(self, bucket) -> None:
        try:
            member = f"serviceAccount:{self._sts_agent_email()}"
            policy = bucket.get_iam_policy(requested_policy_version=3)
            changed = False
            for role in _STS_ROLES:
                binding = next((b for b in policy.bindings if b["role"] == role), None)
                if binding is None:
                    policy.bindings.append({"role": role, "members": {member}})
                    changed = True
                elif member not in binding["members"]:
                    binding["members"] = set(binding["members"]) | {member}
                    changed = True
            if changed:
                bucket.set_iam_policy(policy)
                LOG.info("Granted Storage Transfer agent %s access on gs://%s", member, self.bucket_name)
        except Exception as e:   # STS reports the real error if it matters
            LOG.warning("Could not grant the Storage Transfer agent access on gs://%s (%s). Grant %s manually.",
                        self.bucket_name, e, ", ".join(_STS_ROLES))

    # --------------------------------------------------------------- objects --
    def upload_file(self, local_path: str, object_name: str, retries: int = 3) -> str:
        blob = self.client.bucket(self.bucket_name).blob(object_name)
        for attempt in range(1, retries + 1):
            try:
                blob.upload_from_filename(local_path, timeout=600)
                return f"gs://{self.bucket_name}/{object_name}"
            except Exception as e:
                if attempt == retries:
                    raise
                wait = 5 * 2 ** (attempt - 1)
                LOG.warning("Upload of %s failed (%s), retry %d/%d in %ss", object_name, e, attempt, retries, wait)
                time.sleep(wait)
        raise AssertionError("unreachable")

    def upload_text(self, object_name: str, text: str, content_type: str = "text/plain; charset=utf-8",
                    retries: int = 3) -> str:
        """Uploads a string as-is (UTF-8, line endings untouched) — used for raw SQL definitions."""
        blob = self.client.bucket(self.bucket_name).blob(object_name)
        data = text.encode("utf-8")
        for attempt in range(1, retries + 1):
            try:
                blob.upload_from_string(data, content_type=content_type, timeout=300)
                return f"gs://{self.bucket_name}/{object_name}"
            except Exception as e:
                if attempt == retries:
                    raise
                wait = 5 * 2 ** (attempt - 1)
                LOG.warning("Upload of %s failed (%s), retry %d/%d in %ss", object_name, e, attempt, retries, wait)
                time.sleep(wait)
        raise AssertionError("unreachable")

    def list_names(self, prefix: str) -> list[str]:
        return [b.name for b in self.client.list_blobs(self.bucket_name, prefix=prefix)]

    def delete_names(self, names: list[str]) -> int:
        bucket = self.client.bucket(self.bucket_name)
        for name in names:
            try:
                bucket.blob(name).delete()
            except gexc.NotFound:
                pass
        return len(names)

    def delete_prefix(self, prefix: str) -> int:
        names = self.list_names(prefix)
        bucket = self.client.bucket(self.bucket_name)
        for name in names:
            try:
                bucket.blob(name).delete()
            except gexc.NotFound:
                pass
        return len(names)
