"""
bq_loader.py — everything that touches the target and staging tables.

Per CHUNK (not per 25k-row batch): one load job of gs://.../<chunk>/*.parquet
into the staging table (WRITE_TRUNCATE, explicit schema), then one statement
into the target:

  insert_only  MERGE ... WHEN NOT MATCHED THEN INSERT
               full loads and append-only incremental (re-running a chunk is a no-op)
  upsert       MERGE ... WHEN MATCHED [AND newer] THEN UPDATE ... WHEN NOT MATCHED THEN INSERT
               incremental with a change watermark, partial reloads
  append       INSERT INTO ... SELECT        tables without any key (target was truncated first)

The MERGE source is de-duplicated on the key (QUALIFY ROW_NUMBER() = 1, newest
watermark wins), so a row read twice across page boundaries can never be
inserted twice. DATETIME columns are cast from the staged TIMESTAMP here.

Target tables are clustered on their key (up to 4 clusterable columns) so the
MERGE only reads the blocks it needs, and the schema is kept in step with the
source: new source columns are added; a changed type is an error — except on a
fresh full load, where the table is recreated (it is being reloaded anyway).
"""
from google.api_core.exceptions import NotFound
from google.cloud import bigquery

from src.bigquery.dataset_naming import staging_table_ref, target_table_ref
from src.common import log
from src.planner.type_mapper import CLUSTERABLE, normalize_bq_type

LOG = log.get("bq_loader")


class SchemaDriftError(RuntimeError):
    pass


def bq_ident(name: str) -> str:
    return "`" + str(name).replace("\\", "\\\\").replace("`", "\\`") + "`"


def _source_exprs(columns: list[dict]) -> list[str]:
    out = []
    for c in columns:
        q = bq_ident(c["bq_name"])
        out.append(f"CAST(S0.{q} AS {c['merge_cast']}) AS {q}" if c.get("merge_cast") else f"S0.{q}")
    return out


def build_merge_sql(target_ref: str, staging_ref: str, table_cfg: dict) -> str:
    columns = table_cfg["columns"]
    names = [bq_ident(c["bq_name"]) for c in columns]
    strategy = table_cfg["merge"]
    if strategy == "append":
        return (f"INSERT INTO {bq_ident(target_ref)} ({', '.join(names)})\n"
                f"SELECT {', '.join(_source_exprs(columns))}\nFROM {bq_ident(staging_ref)} AS S0")

    keys = [bq_ident(k) for k in table_cfg["key"]["bq_columns"]]
    if not keys:
        raise ValueError(f"{table_cfg['schema']}.{table_cfg['name']}: merge '{strategy}' needs a key")
    wm = table_cfg.get("watermark")
    wm_col = bq_ident(wm["bq_column"]) if wm and not wm.get("is_key") else None
    order = f" ORDER BY S0.{wm_col} DESC" if wm_col else ""
    using = (f"(\n  SELECT {', '.join(_source_exprs(columns))}\n  FROM {bq_ident(staging_ref)} AS S0\n  WHERE TRUE\n"
             f"  QUALIFY ROW_NUMBER() OVER (PARTITION BY {', '.join('S0.' + k for k in keys)}{order}) = 1\n)")
    on = " AND ".join(f"T.{k} = S.{k}" for k in keys)
    sql = f"MERGE {bq_ident(target_ref)} T\nUSING {using} S\nON {on}\n"
    if strategy == "upsert":
        non_key = [n for n in names if n not in keys]
        if non_key:
            cond = f" AND (T.{wm_col} IS NULL OR S.{wm_col} >= T.{wm_col})" if wm_col else ""
            sets = ", ".join(f"T.{n} = S.{n}" for n in non_key)
            sql += f"WHEN MATCHED{cond} THEN UPDATE SET {sets}\n"
    elif strategy != "insert_only":
        raise ValueError(f"Unknown merge strategy {strategy}")
    sql += f"WHEN NOT MATCHED THEN INSERT ({', '.join(names)}) VALUES ({', '.join('S.' + n for n in names)})"
    return sql


def build_delete_range_sql(target_ref: str, column_bq: str, target_type: str, has_from: bool, has_to: bool) -> str:
    conds = []
    if has_from:
        conds.append(f"{bq_ident(column_bq)} >= CAST(@d_from AS {target_type})")
    if has_to:
        conds.append(f"{bq_ident(column_bq)} < CAST(@d_to AS {target_type})")
    return f"DELETE FROM {bq_ident(target_ref)} WHERE {' AND '.join(conds) or 'FALSE'}"


class BqLoader:
    def __init__(self, settings: dict, client=None):
        self.settings = settings
        self.project_id = settings["gcp"]["project_id"]
        self.client = client or bigquery.Client(project=self.project_id, location=settings["gcp"].get("location"))
        tcfg = (settings.get("gcp") or {}).get("target_tables") or {}
        self.cluster_by_key = bool(tcfg.get("cluster_by_key", True))

    def target_ref(self, cfg: dict) -> str:
        return target_table_ref(self.settings, cfg)

    def staging_ref(self, cfg: dict) -> str:
        return staging_table_ref(self.settings, cfg)

    def _run(self, sql: str, params: list | None = None):
        job = self.client.query(sql, job_config=bigquery.QueryJobConfig(query_parameters=params or []))
        job.result()
        return job

    # ---------------------------------------------------------------- target --
    def _create_target(self, cfg: dict, ref: str) -> None:
        schema = [bigquery.SchemaField(c["bq_name"], c["target_type"]) for c in cfg["columns"]]
        table = bigquery.Table(ref, schema=schema)
        table.description = f"Azure SQL {self.settings['source'].get('server')}/" \
                            f"{self.settings['source'].get('database')} [{cfg['schema']}].[{cfg['name']}]"
        if self.cluster_by_key and cfg["key"]["bq_columns"] and cfg["key"]["kind"] != "synthetic":
            by_name = {c["bq_name"]: c for c in cfg["columns"]}
            fields = [k for k in cfg["key"]["bq_columns"] if by_name[k]["target_type"] in CLUSTERABLE][:4]
            if fields:
                table.clustering_fields = fields
        self.client.create_table(table, exists_ok=True)
        LOG.info("Created %s (%d columns)", ref, len(schema))

    def ensure_target(self, cfg: dict, allow_recreate: bool = False) -> str:
        """Creates the target, adds new source columns, and on a type change
        either recreates it (allow_recreate: fresh full load) or raises."""
        ref = self.target_ref(cfg)
        try:
            table = self.client.get_table(ref)
        except NotFound:
            self._create_target(cfg, ref)
            return "created"
        have = {f.name.lower(): normalize_bq_type(f.field_type) for f in table.schema}
        changed = [(c["bq_name"], have[c["bq_name"].lower()], c["target_type"]) for c in cfg["columns"]
                   if c["bq_name"].lower() in have and have[c["bq_name"].lower()] != c["target_type"]]
        if changed:
            detail = ", ".join(f"{n}: {a} -> {b}" for n, a, b in changed)
            if allow_recreate:
                LOG.warning("%s: column types changed (%s) — recreating it for this full reload", ref, detail)
                self.client.delete_table(ref, not_found_ok=True)
                self._create_target(cfg, ref)
                return "recreated"
            raise SchemaDriftError(f"{ref}: column types differ from the source ({detail}). Run a full reload "
                                   "(--mode full --force) to recreate it, or align types.datetime_as.")
        missing = [c for c in cfg["columns"] if c["bq_name"].lower() not in have]
        if missing:
            LOG.info("%s: adding new source column(s) %s", ref, [c["bq_name"] for c in missing])
            table.schema = list(table.schema) + [bigquery.SchemaField(c["bq_name"], c["target_type"]) for c in missing]
            self.client.update_table(table, ["schema"])
            return "altered"
        return "ok"

    def truncate(self, cfg: dict) -> None:
        self._run(f"TRUNCATE TABLE {bq_ident(self.target_ref(cfg))}")

    def row_count(self, cfg: dict) -> int:
        try:
            return int(self.client.get_table(self.target_ref(cfg)).num_rows or 0)
        except NotFound:
            return 0

    def max_value(self, cfg: dict, bq_column: str):
        try:
            rows = list(self._run(f"SELECT MAX({bq_ident(bq_column)}) AS m FROM {bq_ident(self.target_ref(cfg))}")
                        .result())
        except NotFound:
            return None
        return rows[0].m if rows else None

    def drop_column(self, cfg: dict, bq_column: str) -> None:
        self._run(f"ALTER TABLE {bq_ident(self.target_ref(cfg))} DROP COLUMN IF EXISTS {bq_ident(bq_column)}")

    def delete_range(self, cfg: dict) -> int:
        dr = cfg["filters"]["date_range"]
        col = next(c for c in cfg["columns"] if c["name"] == dr["column"])
        sql = build_delete_range_sql(self.target_ref(cfg), col["bq_name"], col["target_type"],
                                     bool(dr.get("from")), bool(dr.get("to")))
        params = [bigquery.ScalarQueryParameter(n, "STRING", dr[k]) for n, k in (("d_from", "from"), ("d_to", "to"))
                  if dr.get(k)]
        job = self._run(sql, params)
        return int(job.num_dml_affected_rows or 0)

    # --------------------------------------------------------------- staging --
    def load_staging(self, cfg: dict, gcs_uri: str) -> int:
        schema = [bigquery.SchemaField(c["bq_name"], c["staging_type"]) for c in cfg["columns"]]
        job_config = bigquery.LoadJobConfig(source_format=bigquery.SourceFormat.PARQUET,
                                            write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
                                            schema=schema)
        job = self.client.load_table_from_uri(gcs_uri, self.staging_ref(cfg), job_config=job_config)
        job.result()
        return int(job.output_rows or 0)

    def merge(self, cfg: dict) -> int:
        job = self._run(build_merge_sql(self.target_ref(cfg), self.staging_ref(cfg), cfg))
        return int(job.num_dml_affected_rows or 0)

    def drop_staging(self, cfg: dict) -> None:
        self.client.delete_table(self.staging_ref(cfg), not_found_ok=True)
