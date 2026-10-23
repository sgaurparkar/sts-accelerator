"""
table_pipeline.py — migrates ONE table of ONE database (settings resolved by
src/topology/targets.settings_for_target, table_cfg from the planner).

No chunks: a table run reads the source into Parquet files (one per batch_size
page, each uploaded as soon as it is written), lands them in ONE run folder, and
applies them with ONE load job and ONE statement.

Lifecycle (decided from migration_checkpoint, nothing to remember by hand):

  incremental table   1st run   INITIAL: every row (window (-inf, high]) replaces the
                                target in one transaction; high is captured BEFORE
                                reading, so rows changed during the load are picked
                                up by the next run, not lost
                      later     INCREMENTAL: window (last COMPLETED watermark, high],
                                MERGE (upsert / insert-only)
  full table          1st run   FULL: replace in one transaction
                      later     SKIPPED (COMPLETED) unless --force
  partial             MERGE of the filtered rows (--replace-range: range DELETE in the
                      same transaction)

Failure strategy (also migration_checkpoint): while reading, an IN_PROGRESS row
(phase EXTRACTING, last key, files uploaded, run folder, window) is written every
extraction.checkpoint_every_files files; once every file is in GCS, phase LANDED.
The next run of the table RESUMES from the latest such row instead of starting over:

  EXTRACTING  files after the checkpoint are discarded, reading continues after the
              checkpointed key into the SAME run folder, with the SAME window
  LANDED      nothing is read again: load + apply only

The target changes only in the final statement, and every final statement is safe to
repeat, so a crash at any point never leaves it half-loaded or duplicated.
Not resumable (start again): tables without a key (stream strategy) while reading,
a run folder whose files are gone (GCS lifecycle rule), or --force.

stage="gcs" stops after the files are verified in GCS (no load, no COMPLETED).
"""
import datetime
import os
import time
import uuid

from src.bigquery.bq_control_tables import BqControlTables, source_key_values
from src.bigquery.bq_loader import BqLoader
from src.bigquery.dataset_naming import control_table_ref, target_table_name
from src.common import log, values
from src.extraction.parquet_writer import write_rows
from src.extraction.sql_extractor import SqlExtractor, bind_value, filter_conditions
from src.gcs.gcs_manager import GcsManager
from src.metadata.checkpoint_manager import CheckpointManager
from src.metadata.lock_manager import LockManager, lock_key
from src.metadata.metadata_manager import MetadataManager
from src.topology.paths import batch_filename, local_dir, run_rel_dir, run_tag, tag_of
from src.transfer.transport import Transport

RESUMABLE_PHASES = ("EXTRACTING", "LANDED")
REPLACING_KINDS = ("FULL", "INITIAL")


class ReconciliationError(RuntimeError):
    pass


def _label(settings: dict, cfg: dict) -> str:
    s = settings.get("source") or {}
    return f"{s.get('resource_group')}/{s.get('server')}/{s.get('database')}:{cfg['schema']}.{cfg['name']}"


def _wm_greater(a, b, kind: str) -> bool:
    """a > b for watermark values (rowversion compared as unsigned 64-bit)."""
    if kind == "rowversion":
        return int(str(a), 16) > int(str(b), 16)
    return bind_value(a) > bind_value(b)      # aware (BigQuery TIMESTAMP) vs naive (SQL Server) -> both naive UTC


def _remove(paths: list[str]) -> None:
    for p in paths:
        try:
            os.remove(p)
        except OSError:
            pass


def _file_names(settings: dict, cfg: dict, rel: str, count: int) -> list[str]:
    tag = tag_of(rel)
    return [batch_filename(i, settings, cfg["schema"], cfg["name"], cfg["scope_key"], tag) for i in range(count)]


def usable_resume(state: dict | None, settings: dict, cfg: dict, transport, force: bool, stage: str, L) -> dict | None:
    """The unfinished run to continue, or None to start a new run."""
    if force or stage != "bq" or not state or state.get("status") not in ("IN_PROGRESS", "FAILED"):
        return None
    rel, phase, done = state.get("run_folder"), state.get("phase"), int(state.get("files_done") or 0)
    if not rel or phase not in RESUMABLE_PHASES:
        return None
    if phase == "EXTRACTING" and cfg["strategy"] == "stream":
        L.info("The last run stopped while reading a table without a key; it can't continue mid-table — "
               "starting again")
        return None
    if phase == "EXTRACTING" and done and state.get("last_pk") is None:
        return None
    have = transport.landed_names(rel) if phase == "LANDED" else transport.staged_names(rel)
    missing = [n for n in _file_names(settings, cfg, rel, done) if n not in have]
    if missing:
        L.warning("%d file(s) of the unfinished run in %s are gone (lifecycle rule?) — starting again",
                  len(missing), rel)
        return None
    return state


def run_table(settings: dict, logging_config: dict, table_cfg: dict, run_id: str,
              stage: str = "bq", force: bool = False, ignore_locks: bool = False) -> dict:
    label = _label(settings, table_cfg)
    L = log.for_table("pipeline", label)
    if table_cfg.get("skip_reason"):
        L.info("SKIPPED — %s", table_cfg["skip_reason"])
        return {"table": label, "status": "SKIPPED", "reason": table_cfg["skip_reason"], "rows": 0}

    GcsManager(settings).ensure_bucket()
    control = BqControlTables(settings)
    control.ensure_all()
    lock = LockManager(settings, owner=f"{run_id}#{uuid.uuid4().hex[:8]}")
    if ignore_locks:
        lock.enabled = False
    key = lock_key(settings, table_cfg)
    with lock.hold(key):
        return _run_locked(settings, logging_config, table_cfg, run_id, stage, force, label, L, control, lock, key)


def _run_locked(settings, logging_config, cfg, run_id, stage, force, label, L, control, lock, key) -> dict:
    extraction = settings.get("extraction") or {}
    batch_size = int(extraction.get("batch_size", 50000))
    ck_every_files = max(1, int(extraction.get("checkpoint_every_files", 20)))
    keep_local = not bool((settings.get("cleanup") or {}).get("local_files", True))
    validation = settings.get("validation") or {}
    renew_every = max(60, int((settings.get("locking") or {}).get("ttl_seconds", 7200)) // 4)
    mode, strategy = cfg["mode"], cfg["strategy"]
    resumable_read = strategy in ("keyset", "shadow")

    loader = BqLoader(settings)
    checkpoint = CheckpointManager(settings)
    metadata = MetadataManager(logging_config, settings=settings)
    extractor = SqlExtractor(settings)
    transport = Transport(settings)
    started = datetime.datetime.now(datetime.timezone.utc)
    target = f"{settings['gcp']['project_id']}.{settings['gcp']['dataset']}.{cfg['bq_table']}"
    extra = {"scope_key": cfg["scope_key"], "mode": mode}

    state = checkpoint.get_state(cfg)

    # ---- 1. skip a completed full load --------------------------------------------
    if mode == "full" and not force and state and state["status"] == "COMPLETED":
        n = loader.row_count(cfg)
        if n > 0:
            L.info("SKIPPED — full load already COMPLETED (run %s, %s rows in %s). Use --force to reload.",
                   state["run_id"], f"{n:,}", target)
            metadata.log_stage_end(run_id, cfg["name"], "pipeline", "SKIPPED", schema_name=cfg["schema"],
                                   extra={**extra, "reason": "completed", "rows_in_bigquery": n})
            return {"table": label, "status": "SKIPPED", "reason": "already COMPLETED (use --force)", "rows": 0}
        L.info("Checkpoint says COMPLETED but %s is empty — reloading", target)

    metadata.log_stage_start(run_id, cfg["name"], "pipeline", schema_name=cfg["schema"])
    resume = usable_resume(state, settings, cfg, transport, force, stage, L)
    low = high = None
    last_pk, files_done, rows_done, current_file = None, 0, 0, None
    rel, phase, run_kind = None, "EXTRACTING", None

    def save(status, **kw):
        if stage != "bq":
            return
        checkpoint.write(run_id, cfg, status, files_done=files_done, last_pk=last_pk, rows_done=rows_done,
                         low=low, high=high, run_folder=rel, phase=kw.pop("phase", phase), run_kind=run_kind, **kw)

    try:
        # ---- 2. what this run does: resume, initial, incremental, full or partial ----
        if resume:
            low, high = resume.get("low_watermark"), resume.get("high_watermark")
            rel, phase = resume["run_folder"], resume["phase"]
            files_done, rows_done = int(resume.get("files_done") or 0), int(resume.get("rows_done") or 0)
            last_pk = resume.get("last_pk")
            run_kind = resume.get("run_kind") or {"full": "FULL", "partial": "PARTIAL"}.get(mode, "INCREMENTAL")
            L.info("RESUMING %s run %s from the checkpoint: phase %s, %d file(s), %s row(s) already in %s",
                   run_kind, resume.get("run_id"), phase, files_done, f"{rows_done:,}", rel)
        else:
            rel = run_rel_dir(settings, cfg["schema"], cfg["name"], cfg["scope_key"], run_tag(started))
            if mode == "incremental":
                wm = cfg["watermark"]
                if not force:
                    done = state if (state and state["status"] == "COMPLETED") else checkpoint.last_completed(cfg)
                    if done and done.get("watermark") is not None:
                        low = done["watermark"]
                    elif (settings.get("incremental") or {}).get("initial_watermark_from_target", True) \
                            and loader.row_count(cfg) > 0:
                        low = loader.max_value(cfg, wm["bq_column"])
                        if low is not None:
                            L.warning("No incremental checkpoint yet but %s has rows — continuing after "
                                      "MAX(%s) = %s. Rows changed while that data was loaded may be missed; "
                                      "use --force once for a clean INITIAL load.", target, wm["bq_column"], low)
                high = extractor.high_watermark(cfg)            # captured BEFORE reading
                if wm["kind"] == "rowversion":
                    low, high = values.rowversion_hex(low), values.rowversion_hex(high)
                if high is not None and low is not None and not _wm_greater(high, low, wm["kind"]):
                    L.info("No changes since the last run (watermark %s = %s)", wm["column"], low)
                    run_kind = "INCREMENTAL"
                    save("COMPLETED", watermark=low, phase="DONE")
                    _audit(control, settings, cfg, run_id, "NO_CHANGES", 0, 0, started, None, watermark=low)
                    metadata.log_stage_end(run_id, cfg["name"], "pipeline", "NO_CHANGES",
                                           schema_name=cfg["schema"], extra=extra)
                    return {"table": label, "status": "NO_CHANGES", "rows": 0}
                run_kind = "INITIAL" if low is None else "INCREMENTAL"
                L.info("%s load, window on %s: (%s, %s]", run_kind, wm["column"], low, high)
            else:
                run_kind = "FULL" if mode == "full" else "PARTIAL"
        extra["run_kind"] = run_kind

        if stage == "bq" and run_kind not in REPLACING_KINDS:
            loader.ensure_target(cfg, allow_recreate=False)      # schema drift is caught before reading
        conds, params = filter_conditions(cfg, low, high)

        # ---- 3. source -> Parquet files -> staging (one file on disk at a time) --------
        if phase == "EXTRACTING":
            if resume:
                transport.discard_from(rel, files_done)
            if strategy == "shadow":
                extractor.ensure_shadow(cfg, rebuild=not resume)
            L.info("Reading (%s, strategy=%s, %s rows per file)%s into %s", run_kind, strategy, f"{batch_size:,}",
                   f" after key {last_pk}" if last_pk else "", rel)

            def pages():
                if strategy == "stream":
                    for rows in extractor.iter_stream(cfg, batch_size, conds, params):
                        yield rows, None
                else:
                    yield from extractor.iter_keyset(cfg, batch_size, conds, params, resume_after=last_pk)

            local = local_dir(settings, rel)
            last_ck_files, last_ck_time = files_done, time.monotonic()
            save("IN_PROGRESS")
            for rows, last in pages():
                fname = batch_filename(files_done, settings, cfg["schema"], cfg["name"], cfg["scope_key"], tag_of(rel))
                current_file, n, _ = write_rows(rows, cfg["columns"], os.path.join(local, fname))
                transport.stage_file(current_file, rel)
                if not keep_local:
                    _remove([current_file])
                current_file = None
                files_done, rows_done, last_pk = files_done + 1, rows_done + n, last
                if files_done - last_ck_files >= ck_every_files or time.monotonic() - last_ck_time >= renew_every:
                    if resumable_read:
                        save("IN_PROGRESS")
                    lock.renew(key)
                    last_ck_files, last_ck_time = files_done, time.monotonic()
            L.info("Read %s row(s) into %d Parquet file(s)", f"{rows_done:,}", files_done)
            lock.renew(key)
            if files_done:
                transport.land(rel, _file_names(settings, cfg, rel, files_done), description=f"{label} {tag_of(rel)}")
            phase = "LANDED"
            save("IN_PROGRESS")
        uri = f"gs://{settings['gcp']['gcs_bucket']}/{rel}*.parquet" if files_done else None

        if stage == "gcs":
            _audit(control, settings, cfg, run_id, "SUCCESS_GCS_ONLY", files_done, rows_done, started, None)
            metadata.log_stage_end(run_id, cfg["name"], "pipeline", "SUCCESS_GCS_ONLY", schema_name=cfg["schema"],
                                   rows_processed=rows_done, extra={**extra, "gcs": uri})
            L.info("SUCCESS_GCS_ONLY — %s row(s) in %d file(s) landed in %s", f"{rows_done:,}", files_done, uri)
            return {"table": label, "status": "SUCCESS_GCS_ONLY", "rows": rows_done, "files": files_done}

        # ---- 4. ONE load + ONE statement into the target (safe to repeat) ----------------
        dr = (cfg.get("filters") or {}).get("date_range")
        replace_range = bool(mode == "partial" and dr and dr.get("replace"))
        replacing = run_kind in REPLACING_KINDS
        if replacing:
            loader.ensure_target(cfg, allow_recreate=True)
        affected = 0
        if uri:
            staged = loader.load_staging(cfg, uri)
            if staged != rows_done:
                raise RuntimeError(f"extracted {rows_done} rows but BigQuery staged {staged}")
            affected = loader.replace(cfg) if replacing else loader.merge(cfg, replace_range=replace_range)
        elif replacing:
            loader.truncate(cfg)                       # the source (window) is empty
        elif replace_range:
            loader.delete_range(cfg)                   # nothing left in the range at the source
        L.info("%s: %s row(s) loaded, %s affected in %s", run_kind, f"{rows_done:,}", f"{affected:,}", target)

        # ---- 5. finish ----------------------------------------------------------------
        new_wm = (high if high is not None else low) if mode == "incremental" else None
        save("COMPLETED", watermark=new_wm, phase="DONE")
        if uri:
            transport.after_load(rel)

        if strategy == "shadow":
            for step, fn in (("drop synthetic column", lambda: loader.drop_column(cfg, cfg["key"]["bq_columns"][0])),
                             ("drop shadow table", lambda: extractor.drop_shadow(cfg))):
                try:
                    fn()
                except Exception as e:
                    L.warning("Could not %s: %s", step, e)

        source_rows = target_rows = reconciled = None
        if replacing and validation.get("row_count", True):
            source_rows = extractor.count_rows(cfg, conds, params)
            target_rows = loader.row_count(cfg)
            reconciled = source_rows == target_rows
            if reconciled:
                L.info("Reconciled: %s rows in source and BigQuery", f"{source_rows:,}")
            else:
                msg = (f"Row count mismatch: source {source_rows:,} vs BigQuery {target_rows:,} "
                       "(rows changed in the source during the load, or a load problem)")
                if validation.get("fail_on_mismatch", False):
                    raise ReconciliationError(msg)
                L.warning(msg)
        try:
            loader.drop_staging(cfg)
        except Exception as e:
            L.warning("Could not drop staging table: %s", e)

        outcome = "RESUMED_SUCCESS" if resume else "SUCCESS"
        _audit(control, settings, cfg, run_id, outcome, files_done, rows_done, started, None,
               source_rows=source_rows, target_rows=target_rows, reconciled=reconciled, watermark=new_wm)
        metadata.log_stage_end(run_id, cfg["name"], "pipeline", outcome, schema_name=cfg["schema"],
                               rows_processed=rows_done, extra={**extra, "affected": affected, "gcs": uri})
        L.info("%s (%s) — %s row(s) in %d file(s) -> %s", outcome, run_kind, f"{rows_done:,}", files_done, target)
        return {"table": label, "status": outcome, "run_kind": run_kind, "rows": rows_done, "files": files_done,
                "source_rows": source_rows, "target_rows": target_rows, "reconciled": reconciled}

    except Exception as e:
        L.error("FAILED — %s (the next run resumes from the checkpoint where possible)", e)
        if current_file and not keep_local:
            _remove([current_file])
        try:
            save("FAILED")
        except Exception as cp_err:
            L.error("Could not record FAILED checkpoint: %s", cp_err)
        _audit(control, settings, cfg, run_id, "FAILED", files_done, rows_done, started, str(e)[:4000])
        metadata.log_stage_end(run_id, cfg["name"], "pipeline", "FAILED", schema_name=cfg["schema"],
                               error_message=str(e)[:4000], extra=extra)
        raise


def _audit(control, settings, cfg, run_id, outcome, files, rows, started, error,
           source_rows=None, target_rows=None, reconciled=None, watermark=None) -> None:
    table = control_table_ref(settings, (settings.get("metadata") or {}).get("audit_table") or "migration_audit")
    row = {
        **source_key_values(settings),
        "run_id": run_id, "table_name": cfg["name"], "schema_name": cfg["schema"],
        "bq_table": target_table_name(settings, cfg), "load_mode": cfg["mode"], "scope_key": cfg["scope_key"],
        "outcome": outcome, "batches_processed": files, "rows_processed": rows, "resumed_from_batch": None,
        "source_rows": source_rows, "target_rows": target_rows, "reconciled": reconciled,
        "watermark_json": values.dump_one(watermark),
        "started_at": started.isoformat(),
        "finished_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "error_message": error,
    }
    try:
        errors = control.client.insert_rows_json(table, [row])
        if errors:
            log.get("pipeline").warning("Audit row insert failed: %s", errors)
    except Exception as e:
        log.get("pipeline").warning("Audit row insert raised: %s", e)
