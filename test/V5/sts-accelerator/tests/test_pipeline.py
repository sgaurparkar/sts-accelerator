"""End-to-end flow of one table with in-memory SQL Server / GCS / BigQuery fakes:
the full-then-incremental lifecycle and resuming failed runs from migration_checkpoint."""
import datetime
from unittest import mock

import pyarrow.parquet as pq
import pytest

import src.pipeline.table_pipeline as tp
from src.planner.table_planner import plan_table
from src.selection.run_spec import Job
from tests.helpers import HEAP, ORDERS, base_settings, resolved


class Boom(RuntimeError):
    pass


class World:
    """Shared state of the fakes."""

    def __init__(self, rows, tmp_path):
        self.rows = rows                      # source rows (tuples, ORDERS column order)
        self.checkpoints = []                 # rows written by CheckpointManager
        self.target = {}                      # key -> row (BigQuery target)
        self.staged_files = {}                # rel_dir -> {file name: rows}  (direct: GCS)
        self.landed = set()                   # rel_dirs that passed land()
        self.staging = []
        self.calls = []
        self.fail_upload_at = None            # fail when uploading file number N (0-based, per run)
        self.fail_land = False
        self.fail_load = False
        self.high = None
        self.tmp = tmp_path


def make_fakes(world):
    class Extractor:
        def __init__(self, settings):
            pass

        def iter_keyset(self, cfg, batch_size, conds, params, resume_after=None):
            world.calls.append(("extract", resume_after))
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
            world.calls.append(("stream",))
            for i in range(0, len(world.rows), batch_size):
                yield world.rows[i:i + batch_size]

        def high_watermark(self, cfg):
            return world.high

        def count_rows(self, cfg, conds, params):
            rows = world.rows
            if "wm_high" in params and cfg.get("watermark"):
                rows = [r for r in rows if r[3] <= params["wm_high"]]
            return len(rows)

        def ensure_shadow(self, cfg, rebuild):
            pass

    class Transport:
        def __init__(self, settings):
            self.uploads = 0

        def stage_file(self, path, rel_dir):
            if world.fail_upload_at is not None and self.uploads == world.fail_upload_at:
                raise Boom("upload broke")
            self.uploads += 1
            name = path.rsplit("/", 1)[-1]
            world.staged_files.setdefault(rel_dir, {})[name] = pq.read_table(path).to_pylist()
            world.calls.append(("upload", rel_dir, name))
            return name

        def land(self, rel_dir, names, description=""):
            if world.fail_land:
                raise Boom("transfer broke")
            have = world.staged_files.get(rel_dir, {})
            assert all(n in have for n in names) and len(have) == len(names), (sorted(have), names)
            world.landed.add(rel_dir)
            world.calls.append(("land", rel_dir, len(names)))
            return f"gs://b/{rel_dir}*.parquet"

        def staged_names(self, rel_dir):
            return set(world.staged_files.get(rel_dir, {}))

        def landed_names(self, rel_dir):
            return set(world.staged_files.get(rel_dir, {})) if rel_dir in world.landed else set()

        def discard_from(self, rel_dir, first_part):
            from src.topology.paths import part_index
            files = world.staged_files.get(rel_dir, {})
            stale = [n for n in files if part_index(n) >= first_part]
            for n in stale:
                del files[n]
            world.calls.append(("discard", rel_dir, len(stale)))
            return len(stale)

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
            if world.fail_load:
                raise Boom("load broke")
            rel = uri[len("gs://b/"):] if uri.startswith("gs://b/") else uri.split("/", 3)[3]
            rel = rel[: -len("*.parquet")]
            world.calls.append(("load", rel))
            world.staging = [r for _, rows in sorted(world.staged_files[rel].items()) for r in rows]
            return len(world.staging)

        def replace(self, cfg):
            world.calls.append(("replace",))
            world.target.clear()
            return self._apply(cfg, upsert=False)

        def merge(self, cfg, replace_range=False):
            world.calls.append(("merge", replace_range))
            return self._apply(cfg, upsert=cfg["merge"] == "upsert")

        def _apply(self, cfg, upsert):
            n = 0
            keycol = cfg["key"]["bq_columns"][0] if cfg["key"]["bq_columns"] else None
            for r in world.staging:
                k = r[keycol] if keycol else len(world.target)
                if upsert or k not in world.target:
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

        def write(self, run_id, cfg, status, files_done=None, last_pk=None, rows_done=None, low=None, high=None,
                  watermark=None, run_folder=None, phase=None, run_kind=None):
            world.checkpoints.append({"scope": cfg["scope_key"], "status": status, "files_done": files_done,
                                      "last_pk": last_pk, "rows_done": rows_done, "low_watermark": low,
                                      "high_watermark": high, "watermark": watermark, "run_id": run_id,
                                      "run_folder": run_folder, "phase": phase, "run_kind": run_kind})

    return {"SqlExtractor": Extractor, "Transport": Transport, "BqLoader": Loader, "CheckpointManager": Checkpoint,
            "GcsManager": mock.MagicMock(), "BqControlTables": mock.MagicMock(), "LockManager": mock.MagicMock(),
            "MetadataManager": mock.MagicMock()}


def src_rows(n, start=1, day=1):
    return [(i, f"c{i}", None, datetime.datetime(2026, 1, day, 0, 0, i % 60), datetime.datetime(2026, 1, day))
            for i in range(start, start + n)]


@pytest.fixture
def world(tmp_path):
    w = World(src_rows(7), tmp_path)
    w.high = datetime.datetime(2026, 1, 1, 0, 0, 7)
    with mock.patch.multiple(tp, **make_fakes(w)):
        yield w


def run(world, mode="auto", force=False, stage="bq", table=ORDERS, every=20, **job):
    s = resolved(base_settings(world.tmp))
    s["extraction"]["checkpoint_every_files"] = every
    cfg = plan_table(table, s, Job(mode=mode, all_tables=True, **job).validate())
    if cfg["mode"] == "incremental":
        cfg["watermark"] = {"column": "ModifiedDate", "kind": "value", "is_key": False, "bq_column": "ModifiedDate"}
        cfg["merge"] = "upsert"
    world.calls = []
    return tp.run_table(s, {}, cfg, f"run{len(world.checkpoints)}", stage=stage, force=force)


def last(world):
    return world.checkpoints[-1]


def kinds(world, kind):
    return [c for c in world.calls if c[0] == kind]


# ------------------------------------------------------------------ lifecycle --
def test_auto_first_run_is_full_then_incremental_then_no_changes(world):
    r = run(world)
    assert r["status"] == "SUCCESS" and r["run_kind"] == "INITIAL" and r["rows"] == 7 and r["reconciled"]
    assert ("replace",) in world.calls and len(world.target) == 7
    assert last(world)["status"] == "COMPLETED" and last(world)["watermark"] == world.high

    world.rows += src_rows(2, start=8)                                    # two new rows
    world.high = datetime.datetime(2026, 1, 1, 0, 0, 9)
    r = run(world)
    assert r["run_kind"] == "INCREMENTAL" and r["rows"] == 2 and len(world.target) == 9
    assert ("merge", False) in world.calls and ("replace",) not in world.calls

    assert run(world)["status"] == "NO_CHANGES"                          # rerun: no full load again


def test_rows_changed_during_the_initial_load_are_caught_next_run(world):
    world.rows.append((99, "late", None, datetime.datetime(2026, 1, 1, 0, 0, 30), None))  # newer than high
    run(world)
    assert 99 not in world.target                                         # outside (-inf, high]
    world.high = datetime.datetime(2026, 1, 1, 0, 0, 30)
    run(world)
    assert 99 in world.target


def test_table_without_watermark_loads_full_once_then_skips(world):
    world.rows = [("m1", datetime.datetime(2026, 1, 1)), ("m2", datetime.datetime(2026, 1, 2))]
    cfg = plan_table(HEAP, resolved(base_settings(world.tmp)), Job(mode="auto", all_tables=True).validate())
    assert cfg["mode"] == "full" and "full once" in cfg["load_plan"]
    assert run(world, table=HEAP)["status"] == "SUCCESS" and len(world.target) == 2
    assert run(world, table=HEAP)["status"] == "SKIPPED"
    assert run(world, table=HEAP, force=True)["status"] == "SUCCESS"


def test_force_on_incremental_table_is_a_fresh_initial_load(world):
    run(world)
    world.target[123] = {"OrderId": 123}                                  # a row deleted in the source
    r = run(world, force=True)
    assert r["run_kind"] == "INITIAL" and 123 not in world.target


# ------------------------------------------------------------- failure strategy --
def test_failed_read_resumes_from_checkpoint_in_same_folder(world):
    world.fail_upload_at = 2                                              # files 0,1 uploaded, file 2 breaks
    with pytest.raises(Boom):
        run(world)
    failed = last(world)
    assert failed["status"] == "FAILED" and failed["phase"] == "EXTRACTING"
    assert failed["files_done"] == 2 and failed["last_pk"] == {"OrderId": 4} and failed["run_kind"] == "INITIAL"
    assert world.target == {}                                             # target untouched

    world.fail_upload_at = None
    r = run(world)
    assert r["status"] == "RESUMED_SUCCESS" and r["run_kind"] == "INITIAL" and r["rows"] == 7
    assert ("extract", {"OrderId": 4}) in world.calls                    # continued, not restarted
    assert {c[1] for c in kinds(world, "upload")} == {failed["run_folder"]}
    assert len(kinds(world, "upload")) == 2                              # only the missing files
    assert sorted(world.target) == list(range(1, 8))
    assert last(world)["status"] == "COMPLETED"


def test_killed_run_resumes_from_last_in_progress_row(world):
    world.fail_upload_at = 3
    with pytest.raises(Boom):
        run(world, every=1)
    world.checkpoints.pop()                                               # process killed: no FAILED row
    ck = last(world)
    assert ck["status"] == "IN_PROGRESS" and ck["files_done"] == 3
    world.staged_files[ck["run_folder"]]["stray_part-00003.parquet"] = [{"OrderId": 7}]  # half-written leftover
    world.fail_upload_at = None
    r = run(world, every=1)
    assert r["status"] == "RESUMED_SUCCESS" and ("discard", ck["run_folder"], 1) in world.calls
    assert sorted(world.target) == list(range(1, 8))


def test_failure_after_landing_only_reloads(world):
    world.fail_load = True
    with pytest.raises(Boom):
        run(world)
    assert last(world)["phase"] == "LANDED"
    world.fail_load = False
    r = run(world)
    assert r["status"] == "RESUMED_SUCCESS" and not kinds(world, "extract") and not kinds(world, "upload")
    assert len(world.target) == 7


def test_failed_incremental_keeps_its_window_and_never_goes_full(world):
    run(world)                                                            # INITIAL
    world.rows += src_rows(4, start=8)
    world.high = datetime.datetime(2026, 1, 1, 0, 0, 9)                   # window covers rows 8, 9
    world.fail_upload_at = 0
    with pytest.raises(Boom):
        run(world)
    assert last(world)["run_kind"] == "INCREMENTAL"
    world.fail_upload_at = None
    world.high = datetime.datetime(2026, 1, 1, 0, 0, 11)                  # source moved on meanwhile
    r = run(world)
    assert r["status"] == "RESUMED_SUCCESS" and r["run_kind"] == "INCREMENTAL" and r["rows"] == 2
    assert ("replace",) not in world.calls and sorted(world.target) == list(range(1, 10))
    r = run(world)                                                        # next run continues from 9
    assert r["run_kind"] == "INCREMENTAL" and r["rows"] == 2 and sorted(world.target) == list(range(1, 12))


def test_resume_starts_again_when_files_are_gone(world):
    world.fail_upload_at = 2
    with pytest.raises(Boom):
        run(world)
    world.staged_files.clear()                                            # lifecycle rule removed them
    world.fail_upload_at = None
    r = run(world)
    assert r["status"] == "SUCCESS" and ("extract", None) in world.calls and len(world.target) == 7


def test_force_ignores_unfinished_run(world):
    world.fail_upload_at = 2
    with pytest.raises(Boom):
        run(world)
    world.fail_upload_at = None
    r = run(world, force=True)
    assert r["status"] == "SUCCESS" and ("extract", None) in world.calls


def test_stream_table_restarts_after_failed_read(world):
    world.rows = [("m1", datetime.datetime(2026, 1, 1)), ("m2", datetime.datetime(2026, 1, 2)),
                  ("m3", datetime.datetime(2026, 1, 3))]
    world.fail_upload_at = 1
    with pytest.raises(Boom):
        run(world, table=HEAP)
    world.fail_upload_at = None
    r = run(world, table=HEAP)
    assert r["status"] == "SUCCESS" and len(world.target) == 3


# ------------------------------------------------------------------ other modes --
def test_full_mode_is_one_load_and_one_replace(world):
    r = run(world, "full")
    assert r["status"] == "SUCCESS" and r["files"] == 4 and r["run_kind"] == "FULL"
    assert len(kinds(world, "load")) == 1 and ("replace",) in world.calls and ("truncate",) not in world.calls
    uploads = kinds(world, "upload")
    assert len({c[1] for c in uploads}) == 1 and "/run_" in uploads[0][1]
    assert uploads[0][2].endswith("__part-00000.parquet") and "__tbl-Orders__mode-full__run-" in uploads[0][2]
    assert not list(world.tmp.joinpath("data").rglob("*.parquet"))       # local files removed
    assert run(world, "full")["status"] == "SKIPPED"


def test_empty_source_full_load_empties_target(world):
    run(world, "full")
    world.rows = []
    r = run(world, "full", force=True)
    assert r["status"] == "SUCCESS" and r["rows"] == 0 and ("truncate",) in world.calls
    assert not kinds(world, "land") and not kinds(world, "load")


def test_stage_gcs_writes_no_checkpoint(world):
    r = run(world, stage="gcs")
    assert r["status"] == "SUCCESS_GCS_ONLY" and r["rows"] == 7 and world.checkpoints == []
    assert not any(c[0] in ("truncate", "replace", "load", "merge") for c in world.calls)


def test_partial_replace_range_deletes_and_merges_together(world):
    r = run(world, "partial", date_column="updatedAt", date_from="2026-01-01", date_to="2026-02-01",
            replace_range=True)
    assert r["status"] == "SUCCESS" and ("merge", True) in world.calls and ("delete_range",) not in world.calls


def test_staging_row_mismatch_fails_the_run(world):
    def short(self, cfg, uri):
        return 1
    with mock.patch.object(tp.BqLoader, "load_staging", short):
        with pytest.raises(RuntimeError, match="BigQuery staged 1"):
            run(world)
    assert last(world)["status"] == "FAILED"


def test_skipped_plan_is_not_run(world):
    s = resolved(base_settings(world.tmp))
    assert tp.run_table(s, {}, {"schema": "dbo", "name": "x", "skip_reason": "nope"}, "r")["status"] == "SKIPPED"


def test_watermark_compare_mixes_aware_and_naive():
    aware = datetime.datetime(2026, 1, 1, 10, 0, tzinfo=datetime.timezone.utc)
    assert tp._wm_greater(datetime.datetime(2026, 1, 1, 11, 0), aware, "value")
    assert not tp._wm_greater(aware, datetime.datetime(2026, 1, 1, 10, 0), "value")
    assert tp._wm_greater("0x0000000000000800", "0x00000000000007D0", "rowversion")
