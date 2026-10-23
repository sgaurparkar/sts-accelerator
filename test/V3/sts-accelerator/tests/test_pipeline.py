"""End-to-end flow of one table with in-memory SQL Server / GCS / BigQuery fakes."""
import datetime
from unittest import mock

import pyarrow.parquet as pq
import pytest

import src.pipeline.table_pipeline as tp
from src.planner.table_planner import plan_table
from src.selection.run_spec import Job
from tests.helpers import HEAP, ORDERS, base_settings, resolved


class World:
    """Shared state of the fakes."""

    def __init__(self, rows, tmp_path):
        self.rows = rows                      # source rows (tuples, ORDERS column order)
        self.checkpoints = []                 # rows written by CheckpointManager
        self.target = {}                      # key -> row (BigQuery target)
        self.staged = []
        self.calls = []
        self.fail_on_chunk = None
        self.high = None
        self.tmp = tmp_path


def make_fakes(world):
    class Extractor:
        def __init__(self, settings):
            pass

        def iter_keyset(self, cfg, batch_size, conds, params, resume_after=None):
            world.calls.append(("extract", resume_after, tuple(conds)))
            rows = sorted(world.rows, key=lambda r: r[0])
            if "wm_low" in params:
                rows = [r for r in rows if r[3] > params["wm_low"]]
            if "wm_high" in params:
                rows = [r for r in rows if r[3] <= params["wm_high"]]
            if resume_after:
                rows = [r for r in rows if r[0] > resume_after["OrderId"]]
            for i in range(0, len(rows), batch_size):
                page = rows[i:i + batch_size]
                yield page, {"OrderId": page[-1][0]}

        def iter_stream(self, cfg, batch_size, conds, params):
            for i in range(0, len(world.rows), batch_size):
                yield world.rows[i:i + batch_size]

        def high_watermark(self, cfg):
            return world.high

        def count_rows(self, cfg, conds, params):
            return len(world.rows)

    class Transport:
        def __init__(self, settings):
            self.n = 0

        def ship(self, files, rel_dir, description=""):
            self.n += 1
            if world.fail_on_chunk and self.n == world.fail_on_chunk:
                raise RuntimeError("transfer broke")
            world.staged = [r for f in files for r in pq.read_table(f).to_pylist()]
            world.calls.append(("ship", rel_dir, len(files)))
            return f"gs://b/{rel_dir}*.parquet"

        def after_load(self, rel_dir):
            pass

    class Loader:
        def __init__(self, settings):
            pass

        def ensure_target(self, cfg, allow_recreate=False):
            world.calls.append(("ensure_target", allow_recreate))

        def truncate(self, cfg):
            world.calls.append(("truncate",))
            world.target.clear()

        def row_count(self, cfg):
            return len(world.target)

        def max_value(self, cfg, column):
            vals = [r[column] for r in world.target.values() if r.get(column) is not None]
            return max(vals) if vals else None

        def load_staging(self, cfg, uri):
            return len(world.staged)

        def merge(self, cfg):
            n = 0
            keycol = cfg["key"]["bq_columns"][0] if cfg["key"]["bq_columns"] else None
            for r in world.staged:
                k = r[keycol] if keycol else len(world.target)
                if cfg["merge"] == "upsert" or k not in world.target:
                    world.target[k] = r
                    n += 1
            return n

        def delete_range(self, cfg):
            world.calls.append(("delete_range",))
            return 0

        def drop_staging(self, cfg):
            pass

        def drop_column(self, cfg, c):
            pass

    class Checkpoint:
        def __init__(self, settings):
            pass

        def _rows(self, cfg, status=None):
            return [r for r in world.checkpoints if r["scope"] == cfg["scope_key"]
                    and (status is None or r["status"] == status)]

        def get_state(self, cfg):
            rows = self._rows(cfg)
            return dict(rows[-1]) if rows else None

        def last_completed(self, cfg):
            rows = self._rows(cfg, "COMPLETED")
            return dict(rows[-1]) if rows else None

        def write(self, run_id, cfg, status, chunk_index=None, last_pk=None, rows_done=None, low=None, high=None,
                  watermark=None):
            world.checkpoints.append({"scope": cfg["scope_key"], "status": status, "chunk_index": chunk_index,
                                      "last_pk": last_pk, "rows_done": rows_done, "low_watermark": low,
                                      "high_watermark": high, "watermark": watermark, "run_id": run_id})

    return {"SqlExtractor": Extractor, "Transport": Transport, "BqLoader": Loader, "CheckpointManager": Checkpoint,
            "GcsManager": mock.MagicMock(), "BqControlTables": mock.MagicMock(), "LockManager": mock.MagicMock(),
            "MetadataManager": mock.MagicMock()}


def src_rows(n, start=1, day=1):
    return [(i, f"c{i}", None, datetime.datetime(2026, 1, day, 0, 0, i % 60), datetime.datetime(2026, 1, day))
            for i in range(start, start + n)]


@pytest.fixture
def world(tmp_path):
    w = World(src_rows(7), tmp_path)
    with mock.patch.multiple(tp, **make_fakes(w)):
        yield w


def run(world, mode="full", force=False, stage="bq", table=ORDERS, **job):
    s = resolved(base_settings(world.tmp))
    cfg = plan_table(table, s, Job(mode=mode, all_tables=True, **job).validate())
    if mode == "incremental":
        cfg["watermark"] = {"column": "ModifiedDate", "kind": "value", "is_key": False, "bq_column": "ModifiedDate"}
        cfg["merge"] = "upsert"
    return tp.run_table(s, {}, cfg, "run", stage=stage, force=force)


def test_full_load_truncates_chunks_reconciles_and_cleans_up(world):
    r = run(world)
    assert r["status"] == "SUCCESS" and r["rows"] == 7 and r["chunks"] == 2 and r["reconciled"] is True
    assert ("ensure_target", True) in world.calls and ("truncate",) in world.calls
    assert [c[2] for c in world.calls if c[0] == "ship"] == [2, 2]      # 2 files (pages) per chunk; 4 pages
    assert len(world.target) == 7
    assert world.checkpoints[-1]["status"] == "COMPLETED"
    assert not list(world.tmp.joinpath("data").rglob("*.parquet"))       # local files removed


def test_completed_full_is_skipped_then_force_reloads(world):
    run(world)
    assert run(world)["status"] == "SKIPPED"
    world.calls.clear()
    assert run(world, force=True)["status"] == "SUCCESS" and ("truncate",) in world.calls


def test_failed_run_resumes_after_last_committed_chunk(world):
    world.fail_on_chunk = 2
    with pytest.raises(RuntimeError, match="transfer broke"):
        run(world)
    failed = world.checkpoints[-1]
    assert failed["status"] == "FAILED" and failed["last_pk"] == {"OrderId": 4} and failed["chunk_index"] == 0
    world.fail_on_chunk, world.calls = None, []
    r = run(world)
    assert r["status"] == "RESUMED_SUCCESS" and ("truncate",) not in world.calls
    assert ("extract", {"OrderId": 4}, ()) in world.calls and len(world.target) == 7


def test_incremental_window_and_no_changes(world):
    world.high = datetime.datetime(2026, 1, 1, 0, 0, 7)
    r = run(world, "incremental")
    assert r["status"] == "SUCCESS" and len(world.target) == 7
    assert world.checkpoints[-1]["watermark"] == world.high
    assert run(world, "incremental")["status"] == "NO_CHANGES"
    world.rows += src_rows(2, start=8)                                    # two new rows
    world.high = datetime.datetime(2026, 1, 1, 0, 0, 9)
    world.calls.clear()
    r = run(world, "incremental")
    assert r["rows"] == 2 and len(world.target) == 9 and ("truncate",) not in world.calls


def test_incremental_first_run_starts_after_max_in_bigquery(world):
    run(world)                                                            # full load first
    world.rows += src_rows(1, start=8)
    world.high = datetime.datetime(2026, 1, 1, 0, 0, 8)
    r = run(world, "incremental")
    assert r["rows"] == 1                                                 # only the new row was read


def test_stage_gcs_writes_no_checkpoint(world):
    r = run(world, stage="gcs")
    assert r["status"] == "SUCCESS_GCS_ONLY" and r["rows"] == 7 and world.checkpoints == []
    assert ("truncate",) not in world.calls


def test_stream_heap_is_appended_and_never_resumed(world):
    world.rows = [("m1", datetime.datetime(2026, 1, 1)), ("m2", datetime.datetime(2026, 1, 2)),
                  ("m3", datetime.datetime(2026, 1, 3))]
    r = run(world, table=HEAP)
    assert r["status"] == "SUCCESS" and len(world.target) == 3


def test_partial_replace_range_deletes_first(world):
    r = run(world, "partial", date_column="updatedAt", date_from="2026-01-01", date_to="2026-02-01",
            replace_range=True)
    assert r["status"] == "SUCCESS" and ("delete_range",) in world.calls and ("truncate",) not in world.calls


def test_staging_row_mismatch_fails_the_chunk(world):
    def short(cfg, uri):
        return 1
    with mock.patch.object(tp.BqLoader, "load_staging", side_effect=short, autospec=False):
        with pytest.raises(RuntimeError, match="BigQuery staged 1"):
            run(world)
    assert world.checkpoints[-1]["status"] == "FAILED"


def test_skipped_plan_is_not_run(world):
    s = resolved(base_settings(world.tmp))
    assert tp.run_table(s, {}, {"schema": "dbo", "name": "x", "skip_reason": "nope"}, "r")["status"] == "SKIPPED"


def test_watermark_compare_mixes_aware_and_naive():
    aware = datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc)
    assert tp._wm_greater(datetime.datetime(2026, 1, 1, 11, 0), aware, "value")
    assert not tp._wm_greater(aware, datetime.datetime(2026, 1, 1, 10, 0), "value")
    assert tp._wm_greater("0x0000000000000800", "0x00000000000007D0", "rowversion")
