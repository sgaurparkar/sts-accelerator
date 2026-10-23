"""Shared test setup: every process-level cache is cleared between tests."""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(autouse=True)
def _clear_process_caches():
    import src.bigquery.bq_control_tables as bct
    import src.gcp.project_provisioner as pp
    import src.gcs.gcs_manager as gm
    import src.metadata.lock_manager as lm
    for cache in (bct._ENSURED, gm._ENSURED_BUCKETS, pp._ENSURED, lm._LOCAL):
        cache.clear()
    yield
