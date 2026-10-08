"""Verifies a COMPLETED load_mode=full table is skipped (and when it is NOT).
Cloud clients are stubbed, so this runs offline:  python -m pytest tests/test_skip_logic.py"""
import sys
from unittest import mock

# Stub every module that needs cloud SDKs / drivers before importing the pipeline.
for name in ("src.extraction.sql_extractor", "src.extraction.parquet_writer", "src.azure.blob_uploader",
             "src.sts.sts_job_manager", "src.gcs.gcs_manager", "src.bigquery.bq_control_tables",
             "src.bigquery.bq_merge", "src.metadata.metadata_manager", "src.metadata.checkpoint_manager"):
    sys.modules[name] = mock.MagicMock()

import src.pipeline.table_pipeline as tp  # noqa: E402

TABLE = {"name": "t", "schema": "dbo", "primary_key": ["id"], "synthetic_key": False,
         "columns": [{"name": "id", "source_type": "int"}], "load_mode": "full"}
SETTINGS = {"gcp": {}, "metadata": {}, "extraction": {"batch_size": 10}}


def _run(checkpoint_row, rows_in_bq=100, force=False, load_mode="full"):
    cfg = dict(TABLE, load_mode=load_mode)
    cp = tp.CheckpointManager.return_value
    cp.get_checkpoint.return_value = checkpoint_row
    cp.resume_point.return_value = (None, 0, False)
    tp.SqlExtractor.reset_mock()
    # no batches -> a non-skipped run finishes immediately without touching Azure/STS
    tp.SqlExtractor.return_value.extract_batches.return_value = iter(())
    with mock.patch.object(tp, "_count_rows", return_value=rows_in_bq), \
         mock.patch.object(tp, "_write_audit_row"):
        result = tp.run_table(SETTINGS, {}, cfg, "run-1", force=force)
    return result, tp.SqlExtractor.return_value.extract_batches.called


DONE = {"status": "COMPLETED", "run_id": "old", "last_pk": {"id": 5}, "last_batch_index": 1}


def test_completed_full_table_is_skipped():
    result, extracted = _run(DONE)
    assert result["status"] == "SKIPPED" and not extracted


def test_force_reloads():
    result, extracted = _run(DONE, force=True)
    assert result["status"] == "SUCCESS" and extracted


def test_incremental_is_never_skipped():
    result, extracted = _run(DONE, load_mode="incremental")
    assert result["status"] == "SUCCESS" and extracted


def test_empty_target_reloads_despite_completed_checkpoint():
    result, extracted = _run(DONE, rows_in_bq=0)
    assert result["status"] == "SUCCESS" and extracted


def test_failed_checkpoint_is_not_skipped():
    result, extracted = _run(dict(DONE, status="FAILED"))
    assert result["status"] == "SUCCESS" and extracted


def test_no_checkpoint_runs():
    result, extracted = _run(None)
    assert result["status"] == "SUCCESS" and extracted