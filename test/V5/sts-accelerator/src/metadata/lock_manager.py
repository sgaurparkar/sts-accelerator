"""
lock_manager.py — one runner per table at a time.

Without a lock, a triggered full load and the scheduled incremental DAG (or an
Airflow retry next to a zombie task) could work on the same table together:
they share one staging table and one checkpoint, so one run could merge the
other's files. Every table run therefore takes a lease row in
<control dataset>.migration_lock:

  acquire   MERGE: insert the row, or take it over if its lease has expired
  renew     periodically while the table is read (extends expires_at)
  release   DELETE the row (only if we still own it)

A crashed run's lease simply expires (locking.ttl_seconds, default 2 h).
"""
import contextlib
import threading
import time
import uuid

from google.cloud import bigquery

from src.bigquery.bq_control_tables import source_key_values
from src.bigquery.dataset_naming import control_table_ref
from src.common import log

LOG = log.get("lock")
_LOCAL = set()
_LOCAL_GUARD = threading.Lock()


class TableLockedError(RuntimeError):
    pass


def lock_key(settings: dict, table_cfg: dict) -> str:
    k = source_key_values(settings)
    return f"{k['resource_group']}/{k['source_server']}/{k['source_database']}/{table_cfg['schema']}.{table_cfg['name']}".lower()


class LockManager:
    def __init__(self, settings: dict, client=None, owner: str | None = None, sleep=time.sleep):
        cfg = settings.get("locking") or {}
        self.enabled = bool(cfg.get("enabled", True))
        self.ttl = int(cfg.get("ttl_seconds", 7200))
        self.wait_seconds = int(cfg.get("wait_seconds", 0))
        name = (settings.get("metadata") or {}).get("lock_table") or "migration_lock"
        self.table_ref = control_table_ref(settings, name)
        self.settings = settings
        self.owner = owner or uuid.uuid4().hex
        self._client = client
        self._sleep = sleep

    @property
    def client(self):
        if self._client is None:
            self._client = bigquery.Client(project=self.settings["gcp"]["project_id"],
                                           location=self.settings["gcp"].get("location"))
        return self._client

    def _query(self, sql: str, key: str):
        """Runs sql with only the parameters it uses (@k, @o, @ttl)."""
        available = {"k": ("STRING", key), "o": ("STRING", self.owner), "ttl": ("INT64", self.ttl)}
        params = [bigquery.ScalarQueryParameter(n, t, v) for n, (t, v) in available.items() if f"@{n}" in sql]
        return self.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params)).result()

    def _try_acquire(self, key: str) -> str | None:
        try:
            self._query(f"""
                MERGE `{self.table_ref}` T
                USING (SELECT @k AS lock_key) S ON T.lock_key = S.lock_key
                WHEN MATCHED AND (T.expires_at < CURRENT_TIMESTAMP() OR T.owner = @o) THEN
                  UPDATE SET owner = @o, acquired_at = CURRENT_TIMESTAMP(),
                             expires_at = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @ttl SECOND)
                WHEN NOT MATCHED THEN
                  INSERT (lock_key, owner, acquired_at, expires_at)
                  VALUES (@k, @o, CURRENT_TIMESTAMP(), TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @ttl SECOND))
            """, key)
        except Exception as e:       # concurrent DML on the lock table: someone else got there first
            LOG.debug("Lock MERGE for %s raced: %s", key, e)
        rows = list(self._query(f"SELECT owner FROM `{self.table_ref}` WHERE lock_key = @k", key))
        return rows[0].owner if rows else None

    def acquire(self, key: str) -> None:
        with _LOCAL_GUARD:
            if key in _LOCAL:
                raise TableLockedError(f"{key} is already being migrated by this process")
            _LOCAL.add(key)
        if not self.enabled:
            return
        deadline = time.monotonic() + self.wait_seconds
        while True:
            holder = self._try_acquire(key)
            if holder == self.owner:
                return
            if time.monotonic() >= deadline:
                with _LOCAL_GUARD:
                    _LOCAL.discard(key)
                raise TableLockedError(f"{key} is locked by another run (owner {holder}). It is released when that "
                                       f"run ends or its lease expires (locking.ttl_seconds={self.ttl}).")
            self._sleep(30)

    def renew(self, key: str) -> None:
        if not self.enabled:
            return
        try:
            self._query(f"""UPDATE `{self.table_ref}`
                SET expires_at = TIMESTAMP_ADD(CURRENT_TIMESTAMP(), INTERVAL @ttl SECOND)
                WHERE lock_key = @k AND owner = @o""", key)
        except Exception as e:
            LOG.warning("Could not renew lock %s: %s", key, e)

    def release(self, key: str) -> None:
        with _LOCAL_GUARD:
            _LOCAL.discard(key)
        if not self.enabled:
            return
        try:
            self._query(f"DELETE FROM `{self.table_ref}` WHERE lock_key = @k AND owner = @o", key)
        except Exception as e:
            LOG.warning("Could not release lock %s (it expires on its own): %s", key, e)

    def force_release(self, key: str) -> None:
        self._query(f"DELETE FROM `{self.table_ref}` WHERE lock_key = @k", key)

    @contextlib.contextmanager
    def hold(self, key: str):
        self.acquire(key)
        try:
            yield self
        finally:
            self.release(key)
