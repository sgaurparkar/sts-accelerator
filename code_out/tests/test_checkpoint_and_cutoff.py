"""Offline tests for resume-point arithmetic and the date-only cutoff:
python -m pytest tests/test_checkpoint_and_cutoff.py"""
import datetime
import sys
from unittest import mock

for name in ("google", "google.cloud", "google.cloud.bigquery", "dotenv", "sqlalchemy", "sqlalchemy.engine"):
    sys.modules.setdefault(name, mock.MagicMock())

from src.metadata.checkpoint_manager import CheckpointManager  # noqa: E402
from src.extraction.sql_extractor import SqlExtractor  # noqa: E402


def _manager(checkpoint):
    m = CheckpointManager.__new__(CheckpointManager)      # skip the BigQuery client
    return m, checkpoint


def test_resume_after_batch_zero_starts_at_batch_one():
    # regression: `last_batch_index or -1` treated batch 0 as "nothing committed"
    m, cp = _manager({"status": "IN_PROGRESS", "last_batch_index": 0, "last_pk": {"id": 9}, "run_id": "r"})
    assert m.resume_point("t", "dbo", checkpoint=cp) == ({"id": 9}, 1, True)


def test_resume_when_nothing_was_committed_starts_at_zero():
    m, cp = _manager({"status": "FAILED", "last_batch_index": -1, "last_pk": None, "run_id": "r"})
    assert m.resume_point("t", "dbo", checkpoint=cp) == (None, 0, True)
    m, cp = _manager({"status": "FAILED", "last_batch_index": None, "last_pk": None, "run_id": "r"})
    assert m.resume_point("t", "dbo", checkpoint=cp) == (None, 0, True)


def test_resume_mid_table():
    m, cp = _manager({"status": "FAILED", "last_batch_index": 4, "last_pk": {"id": 125000}, "run_id": "r"})
    assert m.resume_point("t", "dbo", checkpoint=cp) == ({"id": 125000}, 5, True)


def test_completed_checkpoint_restarts_from_scratch():
    m, cp = _manager({"status": "COMPLETED", "last_batch_index": 4, "last_pk": {"id": 1}, "run_id": "r"})
    assert m.resume_point("t", "dbo", checkpoint=cp) == (None, 0, False)


def test_date_only_cutoff_includes_the_whole_day():
    next_day = datetime.datetime(2026, 9, 23)
    for cutoff in (datetime.date(2026, 9, 22), "2026-09-22"):
        where, params = SqlExtractor._build_where(["id"], None, cutoff)
        assert where == "WHERE ([updatedAt] < :cutoff_date)"
        assert params == {"cutoff_date": next_day}


def test_timestamp_cutoff_is_used_as_given():
    stamp = datetime.datetime(2026, 9, 22, 13, 30)
    where, params = SqlExtractor._build_where(["id"], None, "2026-09-22T13:30:00")
    assert where == "WHERE ([updatedAt] <= :cutoff_date)" and params == {"cutoff_date": stamp}


def test_cutoff_combines_with_keyset_and_no_cutoff_adds_nothing():
    where, params = SqlExtractor._build_where(["id"], {"id": 7}, datetime.date(2026, 9, 22))
    assert "[id] > :pk_0" in where and "[updatedAt] < :cutoff_date" in where and params["pk_0"] == 7
    assert SqlExtractor._build_where(["id"], None, None) == ("", {})
