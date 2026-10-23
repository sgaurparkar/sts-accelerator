-- Reference copies of what src/bigquery/bq_loader.py generates per CHUNK
-- (print the real statement for any table with tests or bq_loader.build_merge_sql).

-- insert_only (full loads, append-only incremental): re-running a chunk inserts nothing twice
MERGE `{project}.{dataset}.{table}` T
USING (
  SELECT S0.`id`, CAST(S0.`created` AS DATETIME) AS `created`, S0.`amount`
  FROM `{project}.{control_dataset}.stg__{dataset}__{table}` AS S0
  WHERE TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY S0.`id`) = 1
) S
ON T.`id` = S.`id`
WHEN NOT MATCHED THEN INSERT (`id`, `created`, `amount`) VALUES (S.`id`, S.`created`, S.`amount`);

-- upsert (incremental with a change watermark, partial): newest version wins
MERGE `{project}.{dataset}.{table}` T
USING (
  SELECT S0.`id`, CAST(S0.`modified` AS DATETIME) AS `modified`, S0.`amount`
  FROM `{project}.{control_dataset}.stg__{dataset}__{table}` AS S0
  WHERE TRUE
  QUALIFY ROW_NUMBER() OVER (PARTITION BY S0.`id` ORDER BY S0.`modified` DESC) = 1
) S
ON T.`id` = S.`id`
WHEN MATCHED AND (T.`modified` IS NULL OR S.`modified` >= T.`modified`) THEN
  UPDATE SET T.`modified` = S.`modified`, T.`amount` = S.`amount`
WHEN NOT MATCHED THEN INSERT (`id`, `modified`, `amount`) VALUES (S.`id`, S.`modified`, S.`amount`);

-- append (no key; the target was truncated at the start of the full load)
INSERT INTO `{project}.{dataset}.{table}` (`a`, `b`)
SELECT S0.`a`, S0.`b` FROM `{project}.{control_dataset}.stg__{dataset}__{table}` AS S0;
