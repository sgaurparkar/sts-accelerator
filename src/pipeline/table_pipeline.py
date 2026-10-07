import datetime

from src.extraction.sql_extractor import SqlExtractor
from src.extraction.parquet_writer import write_parquet, remove_column
from src.azure.blob_uploader import BlobUploader
from src.sts.sts_job_manager import StsJobManager
from src.gcs.gcs_manager import GcsManager
from src.bigquery.bq_control_tables import BqControlTables
from src.bigquery.bq_merge import BqMerge
from src.metadata.metadata_manager import MetadataManager
from src.metadata.checkpoint_manager import CheckpointManager


def run_table(settings: dict, logging_config: dict, table_cfg: dict, run_id: str,
              stage: str = "bq", force: bool = False) -> dict:
    """
    force: by default a load_mode="full" table whose latest checkpoint is
    COMPLETED (and whose BigQuery target actually holds rows) is SKIPPED —
    nothing is extracted, uploaded, transferred or loaded, and the function
    returns status="SKIPPED". Pass force=True (main.py --force) to re-run it
    anyway. Incremental tables are never skipped: re-checking the source
    for newly appended rows is their whole purpose.

    stage: "bq" (default) runs the full chain through the BigQuery staging
    load + merge, exactly as before. "gcs" stops each batch right after it's
    confirmed present in GCS — the staging load, merge, checkpoint commit,
    and synthetic-key cleanup are all skipped. Because nothing is committed
    to the checkpoint table in "gcs" mode, a later "bq" run for this table
    starts over from batch 0 (re-extract, re-upload, re-transfer); that's
    safe and cheap since STS/GCS overwrite the same batch files, and no
    BigQuery merge has happened yet to redo.
    """
    table_name = table_cfg["name"]
    schema_name = table_cfg["schema"]
    qualified_name = f"{schema_name}.{table_name}"
    metadata_cfg = settings.get("metadata", {})

    metadata = MetadataManager(logging_config, settings["gcp"])
    control_tables = BqControlTables(settings)
    checkpoint = CheckpointManager(settings, metadata_cfg.get("checkpoint_table", "migration_checkpoint"))
    batch_size = settings.get("extraction", {}).get("batch_size", 1000)

    cutoff_date = settings.get("extraction", {}).get("cutoff_date")

    print(f"\n[table_pipeline] ===== Starting pipeline for {qualified_name} =====")
    print(f"[table_pipeline] run_id={run_id} | load_mode={table_cfg['load_mode']} | "
          f"primary_key={table_cfg['primary_key']} | synthetic_key={table_cfg.get('synthetic_key')} | "
          f"batch_size={batch_size} | cutoff_date={cutoff_date or 'disabled'} | stage={stage}")

    print(f"[table_pipeline] [planning] Ensuring BigQuery objects for {qualified_name} exist "
          f"(target/staging in the '{schema_name}'-scoped dataset, plus shared control tables) ...")
    target_ref = control_tables.ensure_target_table(table_cfg)
    staging_ref = control_tables.ensure_staging_table(table_cfg)
    control_tables.ensure_log_table(metadata_cfg.get("log_table", "migration_pipeline_logs"))
    control_tables.ensure_checkpoint_table(metadata_cfg.get("checkpoint_table", "migration_checkpoint"))
    control_tables.ensure_audit_table(metadata_cfg.get("audit_table", "migration_audit"))
    print(f"[table_pipeline] [planning] target={target_ref} | staging={staging_ref}")

    checkpoint_row = checkpoint.get_checkpoint(table_name, schema_name)

    if (not force
            and table_cfg["load_mode"] == "full"
            and checkpoint_row
            and checkpoint_row["status"] == "COMPLETED"):
        # Guard against a stale checkpoint: if someone dropped/emptied the
        # BigQuery target after the last run, COMPLETED no longer means
        # "the data is there", so fall through and reload instead of skipping.
        target_rows = _count_rows(control_tables, target_ref)
        if target_rows > 0:
            print(f"[table_pipeline] ===== {qualified_name}: SKIPPED — already COMPLETED "
                  f"(run_id={checkpoint_row['run_id']}, {target_rows} row(s) in {target_ref}). "
                  f"Use --force to reload. =====")
            metadata.log_stage_end(run_id, table_name, "pipeline", "SKIPPED",
                                    schema_name=schema_name, rows_processed=0,
                                    extra={"reason": "checkpoint COMPLETED",
                                           "completed_run_id": checkpoint_row["run_id"],
                                           "rows_in_bigquery": target_rows})
            # Deliberately NO audit row: the report shows each table's latest
            # audit entry, and a 0-row SKIPPED entry would overwrite the real
            # last-load stats (rows processed, timings) with zeros.
            return {"table": table_name, "status": "SKIPPED",
                    "batches_processed": 0, "rows_processed": 0}
        print(f"[table_pipeline] [planning] {qualified_name} checkpoint says COMPLETED but "
              f"{target_ref} is empty/missing — reloading from batch 0.")

    resume_after, start_batch_index, is_resumed = checkpoint.resume_point(
        table_name, schema_name, checkpoint=checkpoint_row)
    if is_resumed:
        print(f"[table_pipeline] [planning] Resuming {qualified_name} from batch "
              f"{start_batch_index} (last committed key: {resume_after})")
    else:
        print(f"[table_pipeline] [planning] Starting {qualified_name} from batch 0 "
              f"(no in-progress checkpoint found)")

    started_at = datetime.datetime.utcnow()
    batches_processed = 0
    rows_processed = 0
    last_pk = resume_after
    # (batch_index, local_parquet_path) for every batch written this run —
    # only used by the stage='gcs' + synthetic_key post-processing step
    # below, to know exactly which files to strip source_row_id from once
    # every batch is confirmed in GCS.
    batch_parquet_paths: list[tuple[int, str]] = []

    metadata.log_stage_start(run_id, table_name, "pipeline", schema_name=schema_name)

    try:
        extractor = SqlExtractor(settings)
        uploader = BlobUploader(settings)
        sts = StsJobManager(settings)
        gcs = GcsManager(settings)
        merger = BqMerge(settings)

        for batch_index, df in extractor.extract_batches(table_cfg, batch_size, resume_after=resume_after,
                                                           cutoff_date=cutoff_date):
            real_batch_index = start_batch_index + batch_index
            print(f"\n[table_pipeline] --- {qualified_name} | batch {real_batch_index} "
                  f"({len(df)} row(s)) ---")

            print(f"[table_pipeline] [extraction] {qualified_name} batch {real_batch_index}: "
                  f"writing Parquet ...")
            metadata.log_stage_start(run_id, table_name, "extraction", schema_name)
            parquet_path = write_parquet(df, table_name, schema_name, real_batch_index,
                                          columns=table_cfg["columns"])
            batch_parquet_paths.append((real_batch_index, parquet_path))
            metadata.log_stage_end(run_id, table_name, "extraction", "SUCCESS",
                                    schema_name=schema_name, batch_index=real_batch_index,
                                    rows_processed=len(df))
            print(f"[table_pipeline] [extraction] {qualified_name} batch {real_batch_index}: "
                  f"done -> {parquet_path}")

            print(f"[table_pipeline] [upload] {qualified_name} batch {real_batch_index}: "
                  f"uploading to Azure Blob ...")
            metadata.log_stage_start(run_id, table_name, "upload", schema_name)
            blob_path = uploader.upload_file(parquet_path, table_name, schema_name)
            metadata.log_stage_end(run_id, table_name, "upload", "SUCCESS",
                                    schema_name=schema_name, batch_index=real_batch_index,
                                    extra={"blob_path": blob_path})
            print(f"[table_pipeline] [upload] {qualified_name} batch {real_batch_index}: "
                  f"done -> {blob_path}")

            print(f"[table_pipeline] [transfer] {qualified_name} batch {real_batch_index}: "
                  f"starting Azure Blob -> GCS transfer (STS) ...")
            metadata.log_stage_start(run_id, table_name, "transfer", schema_name)
            transfer_result = sts.run_transfer_for_table(table_name, schema_name)
            metadata.log_stage_end(run_id, table_name, "transfer", transfer_result["status"],
                                    schema_name=schema_name, batch_index=real_batch_index,
                                    extra=transfer_result)
            if transfer_result["status"] != "SUCCESS":
                print(f"[table_pipeline] [transfer] {qualified_name} batch {real_batch_index}: "
                      f"FAILED (status={transfer_result['status']})")
                raise RuntimeError(
                    f"Transfer for {table_name} batch {real_batch_index} did not complete "
                    f"(status={transfer_result['status']}): {transfer_result}"
                )
            print(f"[table_pipeline] [transfer] {qualified_name} batch {real_batch_index}: "
                  f"done ({transfer_result.get('objects_copied', '?')} object(s) copied)")

            print(f"[table_pipeline] [verify] {qualified_name} batch {real_batch_index}: "
                  f"confirming file landed in GCS ...")
            if not gcs.batch_file_exists(table_name, schema_name, real_batch_index):
                raise RuntimeError(
                    f"{table_name}_part{real_batch_index:04d}.parquet is missing in GCS "
                    f"after transfer reported success — refusing to load a batch that isn't there."
                )

            if stage == "gcs":
                # Stop here on purpose: the batch is confirmed in GCS, but we
                # skip the BigQuery staging/merge step below. Because the
                # checkpoint contract is "committed once MERGEd", we also
                # deliberately skip checkpoint.commit_batch — so a later
                # "bq" run for this table restarts from batch 0 (re-extract,
                # re-upload, re-transfer) rather than resuming past batches
                # this run never merged. That's safe: STS/GCS just overwrite
                # the same batch files, and no merge has happened yet to redo.
                print(f"[table_pipeline] [stop] {qualified_name} batch {real_batch_index}: "
                      f"stage='gcs' requested — stopping before BigQuery load/merge")
                batches_processed += 1
                rows_processed += len(df)
                continue

            print(f"[table_pipeline] [load] {qualified_name} batch {real_batch_index}: "
                  f"loading to staging and merging into target ...")
            metadata.log_stage_start(run_id, table_name, "load", schema_name)
            merger.load_batch_to_staging(table_cfg, real_batch_index)
            merge_result = merger.merge_batch_into_target(table_cfg)
            metadata.log_stage_end(run_id, table_name, "load", "SUCCESS",
                                    schema_name=schema_name, batch_index=real_batch_index,
                                    rows_processed=len(df), extra=merge_result)
            print(f"[table_pipeline] [load] {qualified_name} batch {real_batch_index}: "
                  f"done ({merge_result.get('rows_affected', '?')} new row(s) inserted "
                  f"into {merge_result.get('table')})")

            pk_cols = table_cfg["primary_key"]
            if pk_cols:
                last_row = df.iloc[-1]
                last_pk = {c: last_row[c] for c in pk_cols}
            checkpoint.commit_batch(run_id, table_cfg, real_batch_index, last_pk)
            print(f"[table_pipeline] [checkpoint] {qualified_name} batch {real_batch_index}: "
                  f"committed (last_pk={last_pk})")

            batches_processed += 1
            rows_processed += len(df)

        if stage == "gcs":
            if table_cfg.get("synthetic_key") and batch_parquet_paths:
                # Every batch is now confirmed in GCS, and stage='gcs' means
                # BigQuery (which would otherwise drop this column from the
                # target table — see drop_synthetic_column below) is being
                # skipped entirely. So the internal source_row_id column has
                # to be stripped right here instead, or it would be left
                # sitting in the "final" Parquet files indefinitely.
                key_col = table_cfg["primary_key"][0]
                print(f"\n[table_pipeline] [cleanup] {qualified_name}: all "
                      f"{len(batch_parquet_paths)} batch(es) confirmed in GCS — stripping "
                      f"internal column '{key_col}' from each Parquet file ...")
                for batch_index, local_path in batch_parquet_paths:
                    remove_column(local_path, key_col)
                    gcs.overwrite_batch_file(local_path, table_name, schema_name, batch_index)
                    if not gcs.batch_file_exists(table_name, schema_name, batch_index):
                        raise RuntimeError(
                            f"{table_name}_part{batch_index:04d}.parquet went missing in GCS "
                            f"after re-uploading the cleaned copy — refusing to report success."
                        )
                print(f"[table_pipeline] [cleanup] {qualified_name}: '{key_col}' removed from "
                      f"every batch file, local and in GCS")

            outcome = "SUCCESS_GCS_ONLY"
            print(f"\n[table_pipeline] ===== {qualified_name}: {outcome} "
                  f"({batches_processed} batch(es), {rows_processed} row(s)) — "
                  f"BigQuery load skipped (stage='gcs') =====")
            metadata.log_stage_end(run_id, table_name, "pipeline", outcome,
                                    schema_name=schema_name, rows_processed=rows_processed)
            _write_audit_row(control_tables, metadata_cfg, run_id, table_cfg, outcome,
                              batches_processed, rows_processed, start_batch_index, started_at, None)
            return {"table": table_name, "status": outcome,
                    "batches_processed": batches_processed, "rows_processed": rows_processed}

        checkpoint.mark_completed(run_id, table_cfg, start_batch_index + batches_processed - 1, last_pk)
        print(f"[table_pipeline] [checkpoint] {qualified_name}: marked COMPLETED")

        if table_cfg.get("synthetic_key"):
            # Only safe to clean up now that the table is fully COMPLETED —
            # if this ran after every batch instead, a run that failed
            # partway through would lose the shadow table (and therefore
            # the stable row numbering) its checkpoint depends on to resume.
            try:
                print(f"[table_pipeline] [cleanup] {qualified_name}: dropping synthetic-key shadow table ...")
                extractor.drop_synthetic_key_table(table_cfg)
                print(f"[table_pipeline] [cleanup] {qualified_name}: shadow table dropped")
            except Exception as cleanup_err:
                print(f"[table_pipeline] WARNING: could not drop shadow table "
                      f"for {table_name}: {cleanup_err}")

            # Mirror that cleanup on the BigQuery side: the source_row_id
            # column was only ever internal plumbing for batching/resuming/
            # merging a table with no real primary key — drop it from the
            # final target table now that every batch is merged in. A
            # failure here is logged but not fatal: the actual data
            # migration already succeeded, and this run is left to try
            # again (drop_synthetic_column is a safe no-op once it's
            # already gone).
            try:
                print(f"[table_pipeline] [cleanup] {qualified_name}: dropping internal "
                      f"'{table_cfg['primary_key'][0]}' column from the BigQuery target table ...")
                merger.drop_synthetic_column(table_cfg)
            except Exception as cleanup_err:
                print(f"[table_pipeline] WARNING: could not drop synthetic column from "
                      f"BigQuery target for {table_name}: {cleanup_err}")

        metadata.log_stage_end(run_id, table_name, "pipeline", "SUCCESS",
                                schema_name=schema_name, rows_processed=rows_processed)

        _write_audit_row(control_tables, metadata_cfg, run_id, table_cfg, "SUCCESS",
                          batches_processed, rows_processed, start_batch_index, started_at, None)
        print(f"\n[table_pipeline] ===== {qualified_name}: SUCCESS "
              f"({batches_processed} batch(es), {rows_processed} row(s)) =====")
        return {"table": table_name, "status": "SUCCESS",
                "batches_processed": batches_processed, "rows_processed": rows_processed}

    except Exception as e:
        print(f"\n[table_pipeline] ===== {qualified_name}: FAILED — {e} =====")
        checkpoint.mark_failed(run_id, table_cfg, start_batch_index + max(batches_processed - 1, 0), last_pk)
        metadata.log_stage_end(run_id, table_name, "pipeline", "FAILED",
                                schema_name=schema_name, error_message=str(e))
        _write_audit_row(control_tables, metadata_cfg, run_id, table_cfg, "FAILED",
                          batches_processed, rows_processed, start_batch_index, started_at, str(e))
        raise


def _count_rows(control_tables: BqControlTables, table_ref: str) -> int:
    """COUNT(*) on a native BigQuery table (answered from metadata, so it
    scans no data). Returns 0 if the table can't be read."""
    try:
        rows = list(control_tables.client.query(f"SELECT COUNT(*) AS n FROM `{table_ref}`").result())
        return int(rows[0].n) if rows else 0
    except Exception as e:
        print(f"[table_pipeline] WARNING: could not count rows in {table_ref}: {e}")
        return 0


def _write_audit_row(control_tables: BqControlTables, metadata_cfg: dict, run_id: str, table_cfg: dict,
                      outcome: str, batches_processed: int, rows_processed: int,
                      resumed_from_batch: int, started_at: datetime.datetime, error_message: str | None) -> None:
    audit_table = metadata_cfg.get("audit_table", "migration_audit")
    table_ref = f"{control_tables.project_id}.{control_tables.dataset}.{audit_table}"
    row = {
        "run_id": run_id,
        "table_name": table_cfg["name"],
        "schema_name": table_cfg["schema"],
        "load_mode": table_cfg["load_mode"],
        "outcome": "RESUMED_SUCCESS" if (outcome == "SUCCESS" and resumed_from_batch > 0) else outcome,
        "batches_processed": batches_processed,
        "rows_processed": rows_processed,
        "resumed_from_batch": resumed_from_batch,
        "started_at": started_at.isoformat(),
        "finished_at": datetime.datetime.utcnow().isoformat(),
        "error_message": error_message,
    }
    errors = control_tables.client.insert_rows_json(table_ref, [row])
    if errors:
        print(f"[table_pipeline] WARNING: audit row insert failed: {errors}")