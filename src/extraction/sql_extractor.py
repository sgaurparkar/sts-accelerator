"""
sql_extractor.py

Pulls data from SQL Server one page at a time using keyset (seek)
pagination on the table's own primary key — never a full unbounded
SELECT * and never a partition-by-column WHERE range. Each call to
extract_batches() is a generator that yields one DataFrame per page,
capped at `batch_size` rows, so the caller can write/upload/merge each
page independently and checkpoint after every one (see
src/metadata/checkpoint_manager.py) — that's what makes a failed run
resumable instead of restarting a table from scratch.

Pagination can resume mid-table: pass `resume_after` (the last
successfully committed primary-key values from the checkpoint table)
and extraction starts right after that row instead of from the top.

Date-based cutoff: pass `cutoff_date` and every page is also filtered
to rows whose `updatedAt` column is on/before that date
(`updatedAt <= cutoff_date`) — i.e. only records that existed as of
the cutoff are migrated. Every source table is expected to have an
`updatedAt` column. This filter is combined with the keyset condition
in the same WHERE clause, so it doesn't disturb resumability — a
resumed run still only re-reads rows after its last committed key, now
additionally restricted to the configured cutoff date.
"""
import datetime

import pandas as pd
from sqlalchemy import text

from src.discovery.shadow_table_naming import shadow_table_name


class SqlExtractor:
    def __init__(self, config: dict):
        self.source_cfg = config["source"]
        if self.source_cfg.get("type") != "mssql":
            raise ValueError(
                f"Unsupported source.type '{self.source_cfg.get('type')}' — "
                "only 'mssql' is supported."
            )
        self._engine = None

    def _get_engine(self):
        if self._engine is None:
            from src.config.connection import build_sql_engine
            self._engine = build_sql_engine(self.source_cfg, fast_executemany=True)
        return self._engine

    @staticmethod
    def _keyset_where(pk_cols: list[str], last_values: dict | None) -> str:
        """Builds the WHERE clause for 'rows strictly after the last page's
        final key', supporting composite primary keys via row-value
        comparison semantics expressed as an OR-chain (SQL Server has no
        native row-value `>` operator on older engines)."""
        if not last_values:
            return ""
        # (k1 > v1) OR (k1 = v1 AND k2 > v2) OR (k1 = v1 AND k2 = v2 AND k3 > v3) ...
        clauses = []
        for i in range(len(pk_cols)):
            parts = [f"[{pk_cols[j]}] = :pk_{j}" for j in range(i)]
            parts.append(f"[{pk_cols[i]}] > :pk_{i}")
            clauses.append("(" + " AND ".join(parts) + ")")
        return "WHERE " + " OR ".join(clauses)

    @staticmethod
    def _cutoff_condition(cutoff_date) -> tuple[str, object]:
        """SQL condition + bound value for `updatedAt <= cutoff_date`.

        A date-only cutoff (YAML turns `2026-09-22` into a date object; a
        "YYYY-MM-DD" string is the same thing) means "through the END of that
        day". Comparing `updatedAt <= '2026-09-22'` would silently drop every row
        updated after 00:00 on the cutoff day, so a date-only cutoff becomes
        `updatedAt < <next day>` instead. A full timestamp is used as given.
        """
        if isinstance(cutoff_date, str):
            text_value = cutoff_date.strip()
            try:
                cutoff_date = (datetime.date.fromisoformat(text_value) if len(text_value) == 10
                               else datetime.datetime.fromisoformat(text_value))
            except ValueError:
                return "[updatedAt] <= :cutoff_date", text_value
        if isinstance(cutoff_date, datetime.datetime):
            return "[updatedAt] <= :cutoff_date", cutoff_date
        if isinstance(cutoff_date, datetime.date):
            next_day = datetime.datetime.combine(cutoff_date, datetime.time.min) + datetime.timedelta(days=1)
            return "[updatedAt] < :cutoff_date", next_day
        return "[updatedAt] <= :cutoff_date", cutoff_date

    @staticmethod
    def _build_where(pk_cols: list[str], last_values: dict | None, cutoff_date: str | None) -> tuple[str, dict]:
        """Combines the keyset (seek) condition with the optional
        date-based incremental filter into a single WHERE clause, so both
        get pushed down to SQL Server together. When `cutoff_date` is
        set, rows are additionally required to have `updatedAt` on/before
        it (a date-only cutoff includes that whole day — see _cutoff_condition) — every source table is expected
        to have an `updatedAt` column."""
        keyset_where = SqlExtractor._keyset_where(pk_cols, last_values)
        params = {}
        conditions = []

        if keyset_where:
            conditions.append(keyset_where[len("WHERE "):])
            for i, c in enumerate(pk_cols):
                params[f"pk_{i}"] = (
                    last_values[c].item() if hasattr(last_values[c], "item") else last_values[c]
                )

        if cutoff_date:
            condition, value = SqlExtractor._cutoff_condition(cutoff_date)
            conditions.append(condition)
            params["cutoff_date"] = value

        if not conditions:
            return "", {}
        return "WHERE " + " AND ".join(f"({c})" for c in conditions), params

    def extract_batches(self, table_cfg: dict, batch_size: int, resume_after: dict | None = None,
                         cutoff_date: str | None = None):
        """Yields (batch_index, DataFrame) pairs, one per page, until the
        table is exhausted. batch_index is 0-based and continues from
        wherever resume_after left off (checkpoint tracks the real index).

        `cutoff_date`, when provided, restricts extraction to rows whose
        `updatedAt` column is on/before that date
        (`updatedAt <= cutoff_date`) — see config/settings.yaml
        (extraction.cutoff_date).

        For tables with no primary key, table_planner.py already set
        table_cfg["primary_key"] = ["source_row_id"] and
        table_cfg["synthetic_key"] = True — the extractor materializes
        that column once in a SQL Server shadow table (see
        _ensure_synthetic_key_table) and it flows through the DataFrame
        just like a real primary key: into Parquet, staging, and the
        BigQuery target table. It stays there — visible, sortable — for
        as long as the table is being migrated, and is only dropped from
        the target once the whole table reaches COMPLETED (see
        table_pipeline.py, which calls BqMerge.drop_synthetic_column)."""
        schema = table_cfg["schema"]
        table = table_cfg["name"]
        key_cols = table_cfg["primary_key"]

        read_schema, read_table = schema, table
        if table_cfg.get("synthetic_key"):
            read_schema, read_table = self._ensure_synthetic_key_table(schema, table, key_cols[0])

        yield from self._extract_batches_by_key(read_schema, read_table, key_cols, batch_size,
                                                 resume_after, cutoff_date)

    def _extract_batches_by_key(self, schema: str, table: str, key_cols: list[str],
                                 batch_size: int, resume_after: dict | None,
                                 cutoff_date: str | None = None):
        """The actual keyset (seek) pagination loop — used identically for
        a real primary key and for a materialized synthetic key. Never
        called with an empty key_cols; extract_batches guarantees one of
        the two exists before reaching here. `cutoff_date` (if set) is
        ANDed into every page's WHERE clause alongside the keyset
        condition — see _build_where."""
        last_values = resume_after
        batch_index = 0
        order_by = ", ".join(f"[{c}]" for c in key_cols)

        if cutoff_date:
            print(f"[sql_extractor] Extracting [{schema}].[{table}] "
                  f"with cutoff_date filter: updatedAt <= {cutoff_date}")
        else:
            print(f"[sql_extractor] Extracting [{schema}].[{table}] (no cutoff_date filter)")

        while True:
            where_clause, params = self._build_where(key_cols, last_values, cutoff_date)

            query = (
                f"SELECT TOP {batch_size} * FROM [{schema}].[{table}] "
                f"{where_clause} ORDER BY {order_by}"
            )

            print(f"[sql_extractor] Fetching batch {batch_index} for [{schema}].[{table}] "
                  f"(up to {batch_size} rows, resume_after={last_values}) ...")
            df = self._run(query, params)
            if df.empty:
                print(f"[sql_extractor] Batch {batch_index} for [{schema}].[{table}] "
                      f"returned 0 rows — extraction complete")
                break

            print(f"[sql_extractor] Fetched {len(df)} row(s) for [{schema}].[{table}] "
                  f"batch {batch_index}")
            yield batch_index, df

            last_row = df.iloc[-1]
            last_values = {
                c: (last_row[c].item() if hasattr(last_row[c], "item") else last_row[c])
                for c in key_cols
            }
            batch_index += 1

            if len(df) < batch_size:
                break

    def _ensure_synthetic_key_table(self, schema: str, table: str, key_col: str) -> tuple[str, str]:
        """Materializes a stable, physically indexed row-number column for
        a table with no primary key, so it can be batched, resumed, and
        merged with the exact same zero-duplicate guarantees a real PK
        gives — instead of pulling the whole table in one unbounded shot.

        Only created once: if the shadow table already exists (e.g. a
        previous run got partway through and this run is resuming), it's
        reused as-is rather than rebuilt, so the row numbers — and
        therefore any in-progress checkpoint — stay stable across runs.
        Safe to call every time; this is a no-op after the first call.

        ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) makes no promise about
        WHICH row gets which number — only that numbering is stable once
        materialized into a real table with a real index on it, which is
        exactly what happens here. That materialization (not the ordering
        clause) is what makes the "no duplicates, no gaps" guarantee hold.
        """
        shadow_table = shadow_table_name(table)
        with self._get_engine().connect() as conn:
            exists = conn.execute(
                text(
                    "SELECT 1 FROM INFORMATION_SCHEMA.TABLES "
                    "WHERE TABLE_SCHEMA = :schema AND TABLE_NAME = :table"
                ),
                {"schema": schema, "table": shadow_table},
            ).first()

            if not exists:
                print(f"[sql_extractor] No primary key on [{schema}].[{table}] — "
                      f"materializing synthetic key shadow table [{schema}].[{shadow_table}] ...")
                conn.execute(text(
                    f"SELECT *, CAST(ROW_NUMBER() OVER (ORDER BY (SELECT NULL)) AS BIGINT) AS [{key_col}] "
                    f"INTO [{schema}].[{shadow_table}] FROM [{schema}].[{table}]"
                ))
                conn.execute(text(
                    f"CREATE UNIQUE CLUSTERED INDEX [IX_{shadow_table}_{key_col}] "
                    f"ON [{schema}].[{shadow_table}] ([{key_col}])"
                ))
                conn.commit()
                print(f"[sql_extractor] Shadow table ready: [{schema}].[{shadow_table}]")
            else:
                print(f"[sql_extractor] Reusing existing shadow table [{schema}].[{shadow_table}]")

        return schema, shadow_table

    def drop_synthetic_key_table(self, table_cfg: dict) -> None:
        """Called by table_pipeline.py only after a synthetic-key table
        is fully COMPLETED (never on partial failure — the shadow table
        must survive to let the next run resume from its checkpoint)."""
        schema = table_cfg["schema"]
        shadow_table = shadow_table_name(table_cfg["name"])
        with self._get_engine().connect() as conn:
            conn.execute(text(f"DROP TABLE IF EXISTS [{schema}].[{shadow_table}]"))
            conn.commit()

    def _run(self, query: str, params: dict | None = None) -> pd.DataFrame:
        with self._get_engine().connect() as conn:
            return pd.read_sql_query(text(query), conn, params=params or {}, coerce_float=False)
