import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from src.pipeline import timing as t


def test_fmt_duration():
    assert t.fmt_duration(12.34) == "12.3s"
    assert t.fmt_duration(75) == "1m 15s"
    assert t.fmt_duration(3725) == "1h 02m 05s"
    assert t.fmt_duration(-5) == "0.0s"


def test_timed_passes_items_and_counts():
    acc = {}
    assert list(t.timed(iter([1, 2, 3]), acc, "sql_read")) == [1, 2, 3]
    assert acc["sql_read"] >= 0


def test_progress_with_and_without_total():
    s = t.progress_line("dbo.customer", 25000, 100000, 60)
    assert "25,000 / 100,000" in s and "25%" in s
    assert "est. remaining 3m 00s" in s and "est. total 4m 00s" in s
    assert "est." not in t.progress_line("dbo.customer", 25000, None, 60)
    assert "(0%)" in t.progress_line("dbo.customer", 0, 100, 5)


def test_breakdown_has_other():
    s = t.breakdown_line({"sql_read": 40.0, "bq_load": 20.0}, 65.0)
    assert "SQL read 40.0s" in s and "BigQuery load+merge 20.0s" in s and "other 5.0s" in s
