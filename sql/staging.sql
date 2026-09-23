-- Reference copy only — src/bigquery/bq_control_tables.py creates this
-- dynamically at runtime (schema/partitioning/clustering taken from
-- src/planner/table_planner.py's dynamic plan for the table), so you
-- never hand-maintain per-table DDL here. Kept for visibility into
-- what gets created.
--
-- Staging mirrors the target schema exactly and holds exactly ONE
-- batch's worth of rows at a time (truncated before each batch load)
-- before bq_merge.py MERGEs it into the target table.
--
-- {dataset} here is the source-schema-scoped dataset (e.g.
-- migrated_data_dbo, migrated_data_sales — see
-- src/bigquery/dataset_naming.py), never the shared base dataset
-- that only holds the operational/control tables.

CREATE TABLE IF NOT EXISTS `{project}.{dataset}.{table}_staging`
LIKE `{project}.{dataset}.{table}`;
