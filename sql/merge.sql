-- Reference copy of the MERGE logic src/bigquery/bq_merge.py runs
-- dynamically, per batch, for every table (full-load or incremental
-- alike). {pk_columns} is the table's real primary key from SQL
-- Server (possibly composite) via src/planner/table_planner.py — this
-- is never hand-typed per table.
--
-- full-load tables:   every matched row is overwritten unconditionally.
-- incremental tables: a matched row is only overwritten if the incoming
--                      watermark is newer, so re-merging an
--                      already-applied batch (the failure-recovery
--                      path) can never clobber newer data.
--
-- {dataset} here is the source-schema-scoped dataset (e.g.
-- migrated_data_dbo, migrated_data_sales — see
-- src/bigquery/dataset_naming.py), never the shared base dataset
-- that only holds the operational/control tables.

MERGE `{project}.{dataset}.{table}` T
USING `{project}.{dataset}.{table}_staging` S
ON {on_clause}                          -- e.g. T.order_id = S.order_id [AND ...]
WHEN MATCHED {watermark_condition} THEN -- omitted entirely for full-load tables
  UPDATE SET *                          -- BigQuery's wildcard update: no "T.*"/"S.*" qualifiers allowed
WHEN NOT MATCHED THEN
  INSERT ROW;
