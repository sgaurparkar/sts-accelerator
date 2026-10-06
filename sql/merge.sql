  MERGE `{project}.{dataset}.{table}` T
  USING `{project}.{dataset}.{table}_staging` S
  ON {on_clause}                          
  WHEN MATCHED {watermark_condition} THEN
    UPDATE SET *                          
  WHEN NOT MATCHED THEN
    INSERT ROW;
