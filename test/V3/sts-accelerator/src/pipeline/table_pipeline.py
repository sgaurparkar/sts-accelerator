"""
table_pipeline.py — migrates ONE table of ONE database (settings resolved by
src/topology/targets.settings_for_target, table_cfg from the planner).

    lock table ─► read state ─► skip? ─► prepare target ─► [incremental window]
       │
       └─► for each CHUNK (batches_per_chunk pages of batch_size rows):
              extract pages -> Parquet files (local)
              ship chunk -> GCS            (one Storage Transfer job, or direct upload)
              ONE load job  gs://.../chunk/*.parquet -> staging   (row count verified)
              ONE MERGE / INSERT staging -> target
              checkpoint (last key, rows, window)  ─ renew lock ─ clean up files
       └─► COMPLETED (+ watermark) ─► reconcile row counts (full) ─► audit ─► unlock

Modes (table_cfg["mode"]):
  full         fresh run: target truncated (recreated if column types changed), all rows
               (optional cutoff); failed run resumes after the last committed key;
               COMPLETED tables are skipped unless force.
  incremental  window (low, high]: low = last committed watermark (first run: MAX of the
               watermark column already in BigQuery), high = captured before reading.
               force re-reads every row (low = none). Upsert by key.
  partial      rows matching the job's filter; upsert by key; replace_range deletes the
               date range in BigQuery first.

stage="gcs" stops after the files are verified in GCS (no load, no checkpoint).
"""
import datetime
import os
import uuid
from dataclasses import dataclass, field

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
from src.topology.paths import batch_filename, chunk_rel_dir, chunk_tag, local_dir
from src.transfer.transport import Transport


class ReconciliationError(RuntimeError):
    pass


@dataclass
class _Chunk:
    index: int
    rel_dir: str
    local: str
    files: list = field(default_factory=list)
    rows: int = 0
    last_key: dict | None = None


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
    per_chunk = max(1, int(extraction.get("batches_per_chunk", 40)))
    keep_local = not bool((settings.get("cleanup") or {}).get("local_files", True))
    validation = settings.get("validation") or {}
    mode, strategy = cfg["mode"], cfg["strategy"]

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

    # ---- 2. resume or start fresh ----------------------------------------------------
    resumable = strategy in ("keyset", "shadow") and stage == "bq"
    resume = bool(not force and resumable and state and state["status"] in ("IN_PROGRESS", "FAILED")
                  and state.get("last_pk") is not None
                  and (mode != "incremental" or state.get("high_watermark") is not None))
    chunk_index = (state["chunk_index"] + 1) if resume else 0
    first_chunk = chunk_index
    rows_done = int(state.get("rows_done") or 0) if resume else 0
    committed_pk = state["last_pk"] if resume else None

    metadata.log_stage_start(run_id, cfg["name"], "pipeline", schema_name=cfg["schema"])
    low = high = None
    chunks_done = 0
    try:
        if stage == "bq":
            if mode == "full" and not resume:
                loader.ensure_target(cfg, allow_recreate=True)
                loader.truncate(cfg)
                L.info("Fresh full load: %s truncated", target)
            else:
                loader.ensure_target(cfg, allow_recreate=False)

        # ---- 3. incremental window ----------------------------------------------------
        if mode == "incremental":
            wm = cfg["watermark"]
            if resume:
                low, high = state["low_watermark"], state["high_watermark"]
            else:
                if not force:
                    done = state if (state and state["status"] == "COMPLETED") else checkpoint.last_completed(cfg)
                    if done and done.get("watermark") is not None:
                        low = done["watermark"]
                    elif (settings.get("incremental") or {}).get("initial_watermark_from_target", True) \
                            and loader.row_count(cfg) > 0:
                        low = loader.max_value(cfg, wm["bq_column"])
                        if low is not None:
                            L.info("No incremental checkpoint yet — starting after MAX(%s) already in BigQuery: %s",
                                   wm["bq_column"], low)
                high = extractor.high_watermark(cfg)
                if wm["kind"] == "rowversion":
                    low, high = values.rowversion_hex(low), values.rowversion_hex(high)
            if high is not None and low is not None and not _wm_greater(high, low, wm["kind"]):
                L.info("No changes since the last run (watermark %s = %s)", wm["column"], low)
                checkpoint.write(run_id, cfg, "COMPLETED", chunk_index=None, rows_done=0, low=low, high=high,
                                 watermark=low)
                _audit(control, settings, cfg, run_id, "NO_CHANGES", 0, 0, 0, started, None, watermark=low)
                metadata.log_stage_end(run_id, cfg["name"], "pipeline", "NO_CHANGES", schema_name=cfg["schema"],
                                       extra=extra)
                return {"table": label, "status": "NO_CHANGES", "rows": 0}
            L.info("Incremental window on %s: (%s, %s]", wm["column"], low, high)

        # ---- 4. partial: mirror deletes in the date range ---------------------------------
        dr = (cfg.get("filters") or {}).get("date_range")
        if mode == "partial" and dr and dr.get("replace") and stage == "bq" and not resume:
            deleted = loader.delete_range(cfg)
            L.info("replace_range: deleted %s row(s) in %s where %s in [%s, %s)", f"{deleted:,}", target,
                   dr["bq_column"], dr.get("from") or "-inf", dr.get("to") or "+inf")

        if strategy == "shadow":
            extractor.ensure_shadow(cfg, rebuild=not resume)

        conds, params = filter_conditions(cfg, low, high)
        L.info("Starting %s load%s | strategy=%s merge=%s | batch=%s rows x %s per chunk | stage=%s",
               mode, f" (resuming after chunk {chunk_index - 1}, key {committed_pk})" if resume else "",
               strategy, cfg["merge"], f"{batch_size:,}", per_chunk, stage)

        # ---- 5. chunks ----------------------------------------------------------------
        def pages():
            if strategy == "stream":
                for rows in extractor.iter_stream(cfg, batch_size, conds, params):
                    yield rows, None
            else:
                yield from extractor.iter_keyset(cfg, batch_size, conds, params, resume_after=committed_pk)

        def chunks():
            current, idx = None, chunk_index
            for rows, last in pages():
                if current is None:
                    rel = chunk_rel_dir(settings, cfg["schema"], cfg["name"], cfg["scope_key"], chunk_tag(idx))
                    current = _Chunk(index=idx, rel_dir=rel, local=local_dir(settings, rel))
                    idx += 1
                path, n, _ = write_rows(rows, cfg["columns"], os.path.join(current.local,
                                                                             batch_filename(len(current.files))))
                current.files.append(path)
                current.rows += n
                current.last_key = last
                if len(current.files) >= per_chunk:
                    yield current
                    current = None
            if current is not None:
                yield current

        # stream reads are spooled to disk first: never hold a source query open while transferring
        chunk_iter = list(chunks()) if strategy == "stream" else chunks()

        for ch in chunk_iter:
            uri = transport.ship(ch.files, ch.rel_dir, description=f"{label} chunk {ch.index}")
            if stage == "gcs":
                rows_done += ch.rows
                chunks_done += 1
                L.info("chunk %d: %s row(s) in %s (stage gcs: not loaded)", ch.index, f"{ch.rows:,}", uri)
                if not keep_local:
                    _remove(ch.files)
                continue
            staged = loader.load_staging(cfg, uri)
            if staged != ch.rows:
                raise RuntimeError(f"chunk {ch.index}: extracted {ch.rows} rows but BigQuery staged {staged}")
            affected = loader.merge(cfg)
            rows_done += ch.rows
            committed_pk = ch.last_key
            chunk_index = ch.index + 1
            chunks_done += 1
            checkpoint.write(run_id, cfg, "IN_PROGRESS", chunk_index=ch.index, last_pk=committed_pk,
                             rows_done=rows_done, low=low, high=high)
            L.info("chunk %d: %s row(s) extracted, %s affected in %s (total %s)", ch.index, f"{ch.rows:,}",
                   f"{affected:,}", target, f"{rows_done:,}")
            metadata.log_stage_end(run_id, cfg["name"], "chunk", "SUCCESS", schema_name=cfg["schema"],
                                   batch_index=ch.index, rows_processed=ch.rows,
                                   extra={**extra, "affected": affected, "gcs": uri})
            transport.after_load(ch.rel_dir)
            if not keep_local:
                _remove(ch.files)
            lock.renew(key)

        if stage == "gcs":
            _audit(control, settings, cfg, run_id, "SUCCESS_GCS_ONLY", chunks_done, rows_done, first_chunk,
                   started, None)
            metadata.log_stage_end(run_id, cfg["name"], "pipeline", "SUCCESS_GCS_ONLY", schema_name=cfg["schema"],
                                   rows_processed=rows_done, extra=extra)
            L.info("SUCCESS_GCS_ONLY — %s row(s) in %d chunk(s) landed in GCS", f"{rows_done:,}", chunks_done)
            return {"table": label, "status": "SUCCESS_GCS_ONLY", "rows": rows_done, "chunks": chunks_done}

        # ---- 6. finish ----------------------------------------------------------------
        new_wm = (high if high is not None else low) if mode == "incremental" else None
        checkpoint.write(run_id, cfg, "COMPLETED", chunk_index=chunk_index - 1 if chunk_index else None,
                         last_pk=committed_pk,
                         rows_done=rows_done, low=low, high=high, watermark=new_wm)

        if strategy == "shadow":
            for step, fn in (("drop synthetic column", lambda: loader.drop_column(cfg, cfg["key"]["bq_columns"][0])),
                             ("drop shadow table", lambda: extractor.drop_shadow(cfg))):
                try:
                    fn()
                except Exception as e:
                    L.warning("Could not %s: %s", step, e)

        source_rows = target_rows = reconciled = None
        if mode == "full" and validation.get("row_count", True):
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
        _audit(control, settings, cfg, run_id, outcome, chunks_done, rows_done, first_chunk, started, None,
               source_rows=source_rows, target_rows=target_rows, reconciled=reconciled, watermark=new_wm)
        metadata.log_stage_end(run_id, cfg["name"], "pipeline", outcome, schema_name=cfg["schema"],
                               rows_processed=rows_done, extra=extra)
        L.info("%s — %s row(s) in %d chunk(s) -> %s", outcome, f"{rows_done:,}", chunks_done, target)
        return {"table": label, "status": outcome, "rows": rows_done, "chunks": chunks_done,
                "source_rows": source_rows, "target_rows": target_rows, "reconciled": reconciled}

    except Exception as e:
        L.error("FAILED — %s", e)
        try:
            checkpoint.write(run_id, cfg, "FAILED", chunk_index=chunk_index - 1 if chunk_index else None,
                             last_pk=committed_pk, rows_done=rows_done, low=low, high=high)
        except Exception as cp_err:
            L.error("Could not record FAILED checkpoint: %s", cp_err)
        _audit(control, settings, cfg, run_id, "FAILED", chunks_done, rows_done, first_chunk, started, str(e)[:4000])
        metadata.log_stage_end(run_id, cfg["name"], "pipeline", "FAILED", schema_name=cfg["schema"],
                               error_message=str(e)[:4000], extra=extra)
        raise


def _audit(control, settings, cfg, run_id, outcome, chunks, rows, resumed_from, started, error,
           source_rows=None, target_rows=None, reconciled=None, watermark=None) -> None:
    table = control_table_ref(settings, (settings.get("metadata") or {}).get("audit_table") or "migration_audit")
    row = {
        **source_key_values(settings),
        "run_id": run_id, "table_name": cfg["name"], "schema_name": cfg["schema"],
        "bq_table": target_table_name(settings, cfg), "load_mode": cfg["mode"], "scope_key": cfg["scope_key"],
        "outcome": outcome, "batches_processed": chunks, "rows_processed": rows, "resumed_from_batch": resumed_from,
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
