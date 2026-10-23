# SQL Server -> BigQuery conversion report

- Target project: `resource1`
- Converted cleanly: **90**  (procedures: 90, views: 0)
- Converted, needs review: **39**
- Failed: **0**   Skipped files: **0**

> `OK` only means no known problem pattern was detected. The output is syntax-converted, never executed:
> dry-run every object in BigQuery and compare results with SQL Server before relying on it.

## [REVIEW] dbo.probe_001_scope_identity  (salesdb/probes/probe_001_scope_identity.sql)  - procedure
- Output: `salesdb/probes/probe_001_scope_identity.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_001_scope_identity`
- **REVIEW**: SCOPE_IDENTITY: no identity columns in BigQuery; generate keys explicitly (e.g. GENERATE_UUID())

## [REVIEW] dbo.probe_002_at_identity  (salesdb/probes/probe_002_at_identity.sql)  - procedure
- Output: `salesdb/probes/probe_002_at_identity.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_002_at_identity`
- **REVIEW**: @@IDENTITY: no identity columns in BigQuery

## [REVIEW] dbo.probe_003_ident_current  (salesdb/probes/probe_003_ident_current.sql)  - procedure
- Output: `salesdb/probes/probe_003_ident_current.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_003_ident_current`
- **REVIEW**: IDENT_CURRENT: no identity columns in BigQuery

## [REVIEW] dbo.probe_004_identity_column  (salesdb/probes/probe_004_identity_column.sql)  - procedure
- Output: `salesdb/probes/probe_004_identity_column.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_004_identity_column`
- **REVIEW**: IDENTITY on column Id dropped; BigQuery has no identity columns

## [REVIEW] dbo.probe_005_sequence  (salesdb/probes/probe_005_sequence.sql)  - procedure
- Output: `salesdb/probes/probe_005_sequence.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_005_sequence`
- **REVIEW**: NEXT VALUE FOR <sequence>: BigQuery has no sequences; use GENERATE_UUID() or ROW_NUMBER() + MAX(existing key)  
  `SELECT NEXT VALUE FOR dbo.Seq1 AS n`

## [OK] dbo.probe_006_newid  (salesdb/probes/probe_006_newid.sql)  - procedure
- Output: `salesdb/probes/probe_006_newid.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_006_newid`

## [OK] dbo.probe_007_error_number  (salesdb/probes/probe_007_error_number.sql)  - procedure
- Output: `salesdb/probes/probe_007_error_number.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_007_error_number`
- **note**: ERROR_NUMBER() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)

## [OK] dbo.probe_008_error_sev_state  (salesdb/probes/probe_008_error_sev_state.sql)  - procedure
- Output: `salesdb/probes/probe_008_error_sev_state.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_008_error_sev_state`
- **note**: ERROR_SEVERITY() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)
- **note**: ERROR_STATE() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)

## [OK] dbo.probe_009_error_line_proc  (salesdb/probes/probe_009_error_line_proc.sql)  - procedure
- Output: `salesdb/probes/probe_009_error_line_proc.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_009_error_line_proc`
- **note**: ERROR_LINE() -> @@error.stack_trace[SAFE_OFFSET(0)].line (a line number of the BigQuery script, not of the original T-SQL)
- **note**: ERROR_PROCEDURE() replaced by the procedure name 'probe_009_error_line_proc' (BigQuery has no equivalent; if the error came from a called procedure, the call stack is in @@error.stack_trace)

## [OK] dbo.probe_010_error_message  (salesdb/probes/probe_010_error_message.sql)  - procedure
- Output: `salesdb/probes/probe_010_error_message.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_010_error_message`

## [OK] dbo.probe_011_at_error  (salesdb/probes/probe_011_at_error.sql)  - procedure
- Output: `salesdb/probes/probe_011_at_error.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_011_at_error`
- **note**: IF @@ERROR ... after a statement converted to BEGIN ... EXCEPTION WHEN ERROR THEN ... END (the error number is not available; @@error.message has the text)

## [OK] dbo.probe_012_raiserror  (salesdb/probes/probe_012_raiserror.sql)  - procedure
- Output: `salesdb/probes/probe_012_raiserror.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_012_raiserror`
- **note**: RAISERROR outside TRY: T-SQL CONTINUES with the next statement, BigQuery RAISE stops the procedure (usually what was intended - check if code follows that should still run)

## [OK] dbo.probe_013_throw_number  (salesdb/probes/probe_013_throw_number.sql)  - procedure
- Output: `salesdb/probes/probe_013_throw_number.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_013_throw_number`

## [OK] dbo.probe_014_xact_state  (salesdb/probes/probe_014_xact_state.sql)  - procedure
- Output: `salesdb/probes/probe_014_xact_state.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_014_xact_state`
- **note**: @@TRANCOUNT / XACT_STATE() emulated with variable v_in_transaction (TRUE between BEGIN TRANSACTION and COMMIT/ROLLBACK of this procedure)

## [OK] dbo.probe_015_trancount  (salesdb/probes/probe_015_trancount.sql)  - procedure
- Output: `salesdb/probes/probe_015_trancount.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_015_trancount`
- **note**: @@TRANCOUNT / XACT_STATE() emulated with variable v_in_transaction (TRUE between BEGIN TRANSACTION and COMMIT/ROLLBACK of this procedure)

## [REVIEW] dbo.probe_016_save_transaction  (salesdb/probes/probe_016_save_transaction.sql)  - procedure
- Output: `salesdb/probes/probe_016_save_transaction.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_016_save_transaction`
- **REVIEW**: savepoints are not supported in BigQuery  
  `SAVE TRANSACTION sp1`
- **REVIEW**: ROLLBACK to a savepoint: BigQuery has no savepoints - this would roll back the WHOLE transaction  
  `ROLLBACK TRANSACTION sp1`

## [OK] dbo.probe_017_nested_tran  (salesdb/probes/probe_017_nested_tran.sql)  - procedure
- Output: `salesdb/probes/probe_017_nested_tran.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_017_nested_tran`
- **note**: nested BEGIN TRANSACTION removed: T-SQL only increments @@TRANCOUNT, BigQuery does not allow nested transactions (the matching inner COMMIT is removed too)

## [OK] dbo.probe_018_xact_abort  (salesdb/probes/probe_018_xact_abort.sql)  - procedure
- Output: `salesdb/probes/probe_018_xact_abort.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_018_xact_abort`

## [OK] dbo.probe_019_transaction_basic  (salesdb/probes/probe_019_transaction_basic.sql)  - procedure
- Output: `salesdb/probes/probe_019_transaction_basic.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_019_transaction_basic`

## [OK] dbo.probe_020_object_id  (salesdb/probes/probe_020_object_id.sql)  - procedure
- Output: `salesdb/probes/probe_020_object_id.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_020_object_id`
- **note**: OBJECT_ID('dbo.T') IS [NOT] NULL -> [NOT] EXISTS on sql_server_centralasia_salesdb.INFORMATION_SCHEMA.TABLES

## [OK] dbo.probe_021_db_name  (salesdb/probes/probe_021_db_name.sql)  - procedure
- Output: `salesdb/probes/probe_021_db_name.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_021_db_name`
- **note**: DB_NAME() replaced by the target dataset name 'sql_server_centralasia_salesdb'

## [REVIEW] dbo.probe_022_host_app  (salesdb/probes/probe_022_host_app.sql)  - procedure
- Output: `salesdb/probes/probe_022_host_app.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_022_host_app`
- **REVIEW**: HOST_NAME: no equivalent
- **REVIEW**: APP_NAME: no equivalent

## [OK] dbo.probe_023_user_fn  (salesdb/probes/probe_023_user_fn.sql)  - procedure
- Output: `salesdb/probes/probe_023_user_fn.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_023_user_fn`
- **note**: SUSER_SNAME() / SYSTEM_USER / CURRENT_USER -> SESSION_USER(): returns the Google account e-mail (user or service account), not the SQL Server login

## [REVIEW] dbo.probe_024_server_vars  (salesdb/probes/probe_024_server_vars.sql)  - procedure
- Output: `salesdb/probes/probe_024_server_vars.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_024_server_vars`
- **REVIEW**: @@SPID: no equivalent
- **REVIEW**: @@SERVERNAME: no equivalent
- **REVIEW**: @@VERSION: no equivalent

## [OK] dbo.probe_025_rowcount  (salesdb/probes/probe_025_rowcount.sql)  - procedure
- Output: `salesdb/probes/probe_025_rowcount.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_025_rowcount`

## [REVIEW] dbo.probe_026_session_context  (salesdb/probes/probe_026_session_context.sql)  - procedure
- Output: `salesdb/probes/probe_026_session_context.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_026_session_context`
- **REVIEW**: SESSION_CONTEXT() does not exist in BigQuery: no equivalent  
  `SESSION_CONTEXT('k')`

## [OK] dbo.probe_027_set_options  (salesdb/probes/probe_027_set_options.sql)  - procedure
- Output: `salesdb/probes/probe_027_set_options.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_027_set_options`

## [OK] dbo.probe_028_stuff  (salesdb/probes/probe_028_stuff.sql)  - procedure
- Output: `salesdb/probes/probe_028_stuff.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_028_stuff`
- **note**: STUFF(s, start, len, rep) -> SUBSTR(s, 1, start-1) || rep || SUBSTR(s, start+len) (T-SQL returns NULL when start is 0 or beyond the string; this version does not)

## [REVIEW] dbo.probe_029_quotename  (salesdb/probes/probe_029_quotename.sql)  - procedure
- Output: `salesdb/probes/probe_029_quotename.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_029_quotename`
- **REVIEW**: QUOTENAME: no equivalent

## [OK] dbo.probe_030_parsename  (salesdb/probes/probe_030_parsename.sql)  - procedure
- Output: `salesdb/probes/probe_030_parsename.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_030_parsename`

## [OK] dbo.probe_031_formatmessage  (salesdb/probes/probe_031_formatmessage.sql)  - procedure
- Output: `salesdb/probes/probe_031_formatmessage.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_031_formatmessage`
- **note**: FORMATMESSAGE -> FORMAT (printf-style; %i/%u became %d)

## [OK] dbo.probe_032_patindex  (salesdb/probes/probe_032_patindex.sql)  - procedure
- Output: `salesdb/probes/probe_032_patindex.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_032_patindex`
- **note**: PATINDEX('%b%', x) -> REGEXP_INSTR(x, '(?s)b') (case-sensitive; SQL Server's default collation is not - set case_sensitivity 'lower' to add (?i))

## [OK] dbo.probe_033_string_split  (salesdb/probes/probe_033_string_split.sql)  - procedure
- Output: `salesdb/probes/probe_033_string_split.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_033_string_split`
- **note**: STRING_SPLIT(s, sep) -> UNNEST(SPLIT(s, sep)) AS value (same rows; the order of the values is not guaranteed in either engine unless enable_ordinal / WITH OFFSET is used)

## [REVIEW] dbo.probe_034_difference  (salesdb/probes/probe_034_difference.sql)  - procedure
- Output: `salesdb/probes/probe_034_difference.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_034_difference`
- **REVIEW**: DIFFERENCE(a, b) -> position-by-position SOUNDEX comparison (0-4); T-SQL's algorithm can score differently for some inputs

## [OK] dbo.probe_035_like_class  (salesdb/probes/probe_035_like_class.sql)  - procedure
- Output: `salesdb/probes/probe_035_like_class.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_035_like_class`
- **note**: LIKE '[a-c]%' uses T-SQL character classes, converted to REGEXP_CONTAINS

## [OK] dbo.probe_036_len_charindex  (salesdb/probes/probe_036_len_charindex.sql)  - procedure
- Output: `salesdb/probes/probe_036_len_charindex.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_036_len_charindex`
- **note**: CHARINDEX/INSTR: SQL Server's default collation finds 'A' in 'abc', BigQuery INSTR is case-sensitive (case_sensitivity 'lower' wraps both sides in LOWER())

## [OK] dbo.probe_037_replicate_space  (salesdb/probes/probe_037_replicate_space.sql)  - procedure
- Output: `salesdb/probes/probe_037_replicate_space.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_037_replicate_space`

## [OK] dbo.probe_038_datalength  (salesdb/probes/probe_038_datalength.sql)  - procedure
- Output: `salesdb/probes/probe_038_datalength.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_038_datalength`
- **note**: DATALENGTH -> BYTE_LENGTH (T-SQL counts 2 bytes per NVARCHAR character, BigQuery counts UTF-8 bytes)

## [OK] dbo.probe_039_format_fn  (salesdb/probes/probe_039_format_fn.sql)  - procedure
- Output: `salesdb/probes/probe_039_format_fn.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_039_format_fn`

## [OK] dbo.probe_040_string_agg  (salesdb/probes/probe_040_string_agg.sql)  - procedure
- Output: `salesdb/probes/probe_040_string_agg.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_040_string_agg`

## [OK] dbo.probe_041_string_agg_wg  (salesdb/probes/probe_041_string_agg_wg.sql)  - procedure
- Output: `salesdb/probes/probe_041_string_agg_wg.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_041_string_agg_wg`

## [OK] dbo.probe_042_char_unicode  (salesdb/probes/probe_042_char_unicode.sql)  - procedure
- Output: `salesdb/probes/probe_042_char_unicode.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_042_char_unicode`

## [OK] dbo.probe_043_collate  (salesdb/probes/probe_043_collate.sql)  - procedure
- Output: `salesdb/probes/probe_043_collate.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_043_collate`

## [OK] dbo.probe_044_concat_isnull  (salesdb/probes/probe_044_concat_isnull.sql)  - procedure
- Output: `salesdb/probes/probe_044_concat_isnull.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_044_concat_isnull`

## [OK] dbo.probe_045_trim_fn  (salesdb/probes/probe_045_trim_fn.sql)  - procedure
- Output: `salesdb/probes/probe_045_trim_fn.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_045_trim_fn`

## [REVIEW] dbo.probe_046_case_insensitive  (salesdb/probes/probe_046_case_insensitive.sql)  - procedure
- Output: `salesdb/probes/probe_046_case_insensitive.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_046_case_insensitive`
- **REVIEW**: string column Name is compared with a text literal: SQL Server's default collation is case-insensitive but BigQuery is case-sensitive, so 'NEW' will not match 'new'. Confirm the data's casing is consistent, or set config 'case_sensitivity': 'lower' to wrap both sides in LOWER()  
  `Name`

## [OK] dbo.probe_047_year_month_day  (salesdb/probes/probe_047_year_month_day.sql)  - procedure
- Output: `salesdb/probes/probe_047_year_month_day.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_047_year_month_day`

## [OK] dbo.probe_048_datepart_name  (salesdb/probes/probe_048_datepart_name.sql)  - procedure
- Output: `salesdb/probes/probe_048_datepart_name.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_048_datepart_name`

## [OK] dbo.probe_049_dateadd_diff  (salesdb/probes/probe_049_dateadd_diff.sql)  - procedure
- Output: `salesdb/probes/probe_049_dateadd_diff.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_049_dateadd_diff`
- **note**: DATEADD on an expression of unknown type: DATE_ADD only accepts DATE - if it is DATETIME/TIMESTAMP use DATETIME_ADD/TIMESTAMP_ADD (or list the column in config 'column_types')

## [OK] dbo.probe_050_eomonth  (salesdb/probes/probe_050_eomonth.sql)  - procedure
- Output: `salesdb/probes/probe_050_eomonth.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_050_eomonth`

## [OK] dbo.probe_051_at_time_zone  (salesdb/probes/probe_051_at_time_zone.sql)  - procedure
- Output: `salesdb/probes/probe_051_at_time_zone.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_051_at_time_zone`
- **note**: x AT TIME ZONE z -> TIMESTAMP(x, z), assuming x is a DATETIME (wall-clock time in z)

## [OK] dbo.probe_052_sysdatetime  (salesdb/probes/probe_052_sysdatetime.sql)  - procedure
- Output: `salesdb/probes/probe_052_sysdatetime.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_052_sysdatetime`

## [REVIEW] dbo.probe_053_isdate  (salesdb/probes/probe_053_isdate.sql)  - procedure
- Output: `salesdb/probes/probe_053_isdate.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_053_isdate`
- **REVIEW**: ISDATE(x) -> IF(SAFE_CAST(x AS DATETIME/DATE) IS NOT NULL, 1, 0): only ISO strings count as dates in BigQuery ('Jan 1 2020', '01/02/2020' return 0 here, 1 in T-SQL)

## [OK] dbo.probe_054_convert_style  (salesdb/probes/probe_054_convert_style.sql)  - procedure
- Output: `salesdb/probes/probe_054_convert_style.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_054_convert_style`

## [OK] dbo.probe_055_try_convert  (salesdb/probes/probe_055_try_convert.sql)  - procedure
- Output: `salesdb/probes/probe_055_try_convert.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_055_try_convert`

## [REVIEW] dbo.probe_056_try_parse  (salesdb/probes/probe_056_try_parse.sql)  - procedure
- Output: `salesdb/probes/probe_056_try_parse.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_056_try_parse`
- **REVIEW**: TRY_PARSE(x AS DATE) -> SAFE_CAST: BigQuery only parses ISO formats (YYYY-MM-DD, plain numbers); T-SQL also accepts culture formats ('12/31/2020', '$1,000') - use SAFE.PARSE_DATE('%m/%d/%Y', x) for those  
  `TRY_PARSE(Name AS DATE)`

## [REVIEW] dbo.probe_057_isnumeric  (salesdb/probes/probe_057_isnumeric.sql)  - procedure
- Output: `salesdb/probes/probe_057_isnumeric.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_057_isnumeric`
- **REVIEW**: ISNUMERIC(x) -> IF(SAFE_CAST(x AS FLOAT64) IS NOT NULL, 1, 0): T-SQL also returns 1 for '$', ',', '.', '-', '1e5d' ... and 0 for 'NaN'/'inf'; check the inputs you expect

## [OK] dbo.probe_058_choose_iif  (salesdb/probes/probe_058_choose_iif.sql)  - procedure
- Output: `salesdb/probes/probe_058_choose_iif.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_058_choose_iif`

## [REVIEW] dbo.probe_059_hashbytes  (salesdb/probes/probe_059_hashbytes.sql)  - procedure
- Output: `salesdb/probes/probe_059_hashbytes.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_059_hashbytes`
- **REVIEW**: HASHBYTES: use MD5()/SHA256()/SHA512()

## [REVIEW] dbo.probe_060_checksum  (salesdb/probes/probe_060_checksum.sql)  - procedure
- Output: `salesdb/probes/probe_060_checksum.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_060_checksum`
- **REVIEW**: CHECKSUM: use FARM_FINGERPRINT()
- **REVIEW**: BINARY_CHECKSUM: use FARM_FINGERPRINT()

## [REVIEW] dbo.probe_061_rand_seed  (salesdb/probes/probe_061_rand_seed.sql)  - procedure
- Output: `salesdb/probes/probe_061_rand_seed.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_061_rand_seed`
- **REVIEW**: RAND(seed): BigQuery RAND() takes no seed, so the value is not repeatable  
  `RAND(42)`

## [OK] dbo.probe_062_int_division  (salesdb/probes/probe_062_int_division.sql)  - procedure
- Output: `salesdb/probes/probe_062_int_division.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_062_int_division`

## [OK] dbo.probe_063_modulo_bitwise  (salesdb/probes/probe_063_modulo_bitwise.sql)  - procedure
- Output: `salesdb/probes/probe_063_modulo_bitwise.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_063_modulo_bitwise`

## [OK] dbo.probe_064_stdev_var  (salesdb/probes/probe_064_stdev_var.sql)  - procedure
- Output: `salesdb/probes/probe_064_stdev_var.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_064_stdev_var`

## [OK] dbo.probe_065_percentile_cont  (salesdb/probes/probe_065_percentile_cont.sql)  - procedure
- Output: `salesdb/probes/probe_065_percentile_cont.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_065_percentile_cont`

## [OK] dbo.probe_066_rollup_grouping  (salesdb/probes/probe_066_rollup_grouping.sql)  - procedure
- Output: `salesdb/probes/probe_066_rollup_grouping.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_066_rollup_grouping`

## [OK] dbo.probe_067_top_isnull_getdate  (salesdb/probes/probe_067_top_isnull_getdate.sql)  - procedure
- Output: `salesdb/probes/probe_067_top_isnull_getdate.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_067_top_isnull_getdate`

## [OK] dbo.probe_068_cross_apply  (salesdb/probes/probe_068_cross_apply.sql)  - procedure
- Output: `salesdb/probes/probe_068_cross_apply.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_068_cross_apply`
- **note**: CROSS APPLY -> CROSS JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) AS x (correlated; dry-run it - BigQuery rejects correlated subqueries it cannot de-correlate)

## [OK] dbo.probe_069_outer_apply  (salesdb/probes/probe_069_outer_apply.sql)  - procedure
- Output: `salesdb/probes/probe_069_outer_apply.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_069_outer_apply`
- **note**: OUTER APPLY -> LEFT JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) AS x (correlated; dry-run it - BigQuery rejects correlated subqueries it cannot de-correlate)

## [REVIEW] dbo.probe_070_output_clause  (salesdb/probes/probe_070_output_clause.sql)  - procedure
- Output: `salesdb/probes/probe_070_output_clause.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_070_output_clause`
- **REVIEW**: OUTPUT: OUTPUT clause is not supported

## [OK] dbo.probe_071_pivot  (salesdb/probes/probe_071_pivot.sql)  - procedure
- Output: `salesdb/probes/probe_071_pivot.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_071_pivot`
- **note**: PIVOT: converted to BigQuery PIVOT syntax; compare the result columns once

## [OK] dbo.probe_072_unpivot  (salesdb/probes/probe_072_unpivot.sql)  - procedure
- Output: `salesdb/probes/probe_072_unpivot.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_072_unpivot`
- **note**: UNPIVOT: converted to BigQuery UNPIVOT syntax; compare the result columns once

## [OK] dbo.probe_073_top_ties  (salesdb/probes/probe_073_top_ties.sql)  - procedure
- Output: `salesdb/probes/probe_073_top_ties.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_073_top_ties`
- **note**: TOP n WITH TIES converted to QUALIFY RANK() OVER (ORDER BY ...) <= n

## [OK] dbo.probe_074_top_percent  (salesdb/probes/probe_074_top_percent.sql)  - procedure
- Output: `salesdb/probes/probe_074_top_percent.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_074_top_percent`
- **note**: TOP n PERCENT converted to QUALIFY ROW_NUMBER() OVER (ORDER BY ...) <= CEIL(COUNT(*) OVER () * p / 100)

## [OK] dbo.probe_075_for_xml_path  (salesdb/probes/probe_075_for_xml_path.sql)  - procedure
- Output: `salesdb/probes/probe_075_for_xml_path.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_075_for_xml_path`
- **note**: STUFF((SELECT sep + x ... FOR XML PATH('')), 1, n, '') converted to STRING_AGG (note: FOR XML also escaped &, <, > as &amp; &lt; &gt; - STRING_AGG does not)

## [REVIEW] dbo.probe_076_xml_value  (salesdb/probes/probe_076_xml_value.sql)  - procedure
- Output: `salesdb/probes/probe_076_xml_value.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_076_xml_value`
- **REVIEW**: variable @x: XML mapped to STRING

## [OK] dbo.probe_077_for_json  (salesdb/probes/probe_077_for_json.sql)  - procedure
- Output: `salesdb/probes/probe_077_for_json.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_077_for_json`
- **note**: FOR JSON PATH -> TO_JSON_STRING(ARRAY(SELECT AS STRUCT ...)) - NULL columns appear as "col":null in BigQuery but are omitted by FOR JSON (add INCLUDE_NULL_VALUES semantics or strip them if consumers care)

## [REVIEW] dbo.probe_078_openjson  (salesdb/probes/probe_078_openjson.sql)  - procedure
- Output: `salesdb/probes/probe_078_openjson.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_078_openjson`
- **REVIEW**: OPENJSON: use JSON functions / UNNEST(JSON_QUERY_ARRAY(...))

## [OK] dbo.probe_079_json_value  (salesdb/probes/probe_079_json_value.sql)  - procedure
- Output: `salesdb/probes/probe_079_json_value.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_079_json_value`

## [REVIEW] dbo.probe_080_openquery  (salesdb/probes/probe_080_openquery.sql)  - procedure
- Output: `salesdb/probes/probe_080_openquery.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_080_openquery`
- **REVIEW**: OPENQUERY: linked servers not supported

## [REVIEW] dbo.probe_081_four_part  (salesdb/probes/probe_081_four_part.sql)  - procedure
- Output: `salesdb/probes/probe_081_four_part.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_081_four_part`
- **REVIEW**: four-part name LinkedSrv.OtherDb.dbo.T: linked servers are not reachable from BigQuery (load the data or use a federated/external table)  
  `LinkedSrv.OtherDb.dbo.T`

## [OK] dbo.probe_082_nolock  (salesdb/probes/probe_082_nolock.sql)  - procedure
- Output: `salesdb/probes/probe_082_nolock.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_082_nolock`

## [OK] dbo.probe_083_option_recompile  (salesdb/probes/probe_083_option_recompile.sql)  - procedure
- Output: `salesdb/probes/probe_083_option_recompile.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_083_option_recompile`

## [REVIEW] dbo.probe_084_insert_exec  (salesdb/probes/probe_084_insert_exec.sql)  - procedure
- Output: `salesdb/probes/probe_084_insert_exec.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_084_insert_exec`
- **REVIEW**: INSERT ... EXEC proc: BigQuery cannot insert a procedure's result set - let the called procedure write into a TEMP TABLE (temp tables are visible to the caller in the same script) or turn it into a TABLE FUNCTION and INSERT ... SELECT from it  
  `INSERT INTO #r EXEC dbo.usp_Other`

## [OK] dbo.probe_085_select_into  (salesdb/probes/probe_085_select_into.sql)  - procedure
- Output: `salesdb/probes/probe_085_select_into.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_085_select_into`

## [OK] dbo.probe_086_recursive_cte  (salesdb/probes/probe_086_recursive_cte.sql)  - procedure
- Output: `salesdb/probes/probe_086_recursive_cte.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_086_recursive_cte`
- **note**: recursive CTE n: WITH RECURSIVE added (BigQuery stops after 500 iterations; T-SQL's default MAXRECURSION is 100, OPTION (MAXRECURSION 0) = no limit)

## [OK] dbo.probe_087_merge_basic  (salesdb/probes/probe_087_merge_basic.sql)  - procedure
- Output: `salesdb/probes/probe_087_merge_basic.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_087_merge_basic`

## [REVIEW] dbo.probe_088_merge_output  (salesdb/probes/probe_088_merge_output.sql)  - procedure
- Output: `salesdb/probes/probe_088_merge_output.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_088_merge_output`
- **REVIEW**: OUTPUT: OUTPUT clause is not supported

## [REVIEW] dbo.probe_089_goto  (salesdb/probes/probe_089_goto.sql)  - procedure
- Output: `salesdb/probes/probe_089_goto.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_089_goto`
- **REVIEW**: label lbl: BigQuery has no GOTO/labels; restructure with IF / LOOP / BREAK  
  `lbl:`
- **REVIEW**: GOTO is not supported; rewrite manually  
  `GOTO lbl`

## [REVIEW] dbo.probe_090_waitfor  (salesdb/probes/probe_090_waitfor.sql)  - procedure
- Output: `salesdb/probes/probe_090_waitfor.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_090_waitfor`
- **REVIEW**: WAITFOR is not supported; rewrite manually  
  `WAITFOR DELAY '00:00:01'`

## [OK] dbo.probe_091_fulltext  (salesdb/probes/probe_091_fulltext.sql)  - procedure
- Output: `salesdb/probes/probe_091_fulltext.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_091_fulltext`
- **note**: full-text CONTAINS -> CONTAINS_SUBSTR (case-insensitive SUBSTRING match, not word match: 'cat' also matches 'concatenate'); consider SEARCH()

## [OK] dbo.probe_092_table_variable  (salesdb/probes/probe_092_table_variable.sql)  - procedure
- Output: `salesdb/probes/probe_092_table_variable.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_092_table_variable`

## [OK] dbo.probe_093_global_temp  (salesdb/probes/probe_093_global_temp.sql)  - procedure
- Output: `salesdb/probes/probe_093_global_temp.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_093_global_temp`
- **note**: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
- **note**: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
- **note**: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery

## [OK] dbo.probe_094_update_join  (salesdb/probes/probe_094_update_join.sql)  - procedure
- Output: `salesdb/probes/probe_094_update_join.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_094_update_join`
- **note**: UPDATE ... FROM: BigQuery raises an error if a target row matches more than one source row (T-SQL silently uses one of them) - make sure the joined source is unique on the join key (aggregate it first if needed)

## [OK] dbo.probe_095_delete_join  (salesdb/probes/probe_095_delete_join.sql)  - procedure
- Output: `salesdb/probes/probe_095_delete_join.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_095_delete_join`

## [REVIEW] dbo.probe_096_update_top  (salesdb/probes/probe_096_update_top.sql)  - procedure
- Output: `salesdb/probes/probe_096_update_top.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_096_update_top`
- **REVIEW**: UPDATE TOP (n) has no BigQuery equivalent: rewrite as UPDATE ... WHERE key IN (SELECT key FROM ... ORDER BY ... LIMIT n)  
  `UPDATE TOP (10) dbo.T SET Name = 'x'`

## [REVIEW] dbo.probe_097_delete_top  (salesdb/probes/probe_097_delete_top.sql)  - procedure
- Output: `salesdb/probes/probe_097_delete_top.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_097_delete_top`
- **REVIEW**: DELETE TOP (n) has no BigQuery equivalent: rewrite as DELETE ... WHERE key IN (SELECT key FROM ... ORDER BY ... LIMIT n)  
  `DELETE TOP (10) FROM dbo.T WHERE Id > 0`

## [OK] dbo.probe_098_identity_insert  (salesdb/probes/probe_098_identity_insert.sql)  - procedure
- Output: `salesdb/probes/probe_098_identity_insert.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_098_identity_insert`
- **note**: SET IDENTITY_INSERT removed (BigQuery has no identity columns; explicit values are always accepted)

## [OK] dbo.probe_099_cursor_basic  (salesdb/probes/probe_099_cursor_basic.sql)  - procedure
- Output: `salesdb/probes/probe_099_cursor_basic.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_099_cursor_basic`
- **note**: cursor c converted to FOR loop (BigQuery has no cursors)

## [REVIEW] dbo.probe_100_cursor_scroll  (salesdb/probes/probe_100_cursor_scroll.sql)  - procedure
- Output: `salesdb/probes/probe_100_cursor_scroll.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_100_cursor_scroll`
- **REVIEW**: cursors are not supported; rewrite as FOR record IN (SELECT ...) DO ... END FOR  
  `DECLARE c CURSOR SCROLL FOR SELECT Id FROM dbo.T`
- **REVIEW**: OPEN is not supported; rewrite manually  
  `OPEN c`
- **REVIEW**: FETCH is not supported; rewrite manually  
  `FETCH LAST FROM c INTO @i`
- **REVIEW**: FETCH is not supported; rewrite manually  
  `FETCH PRIOR FROM c INTO @i`
- **REVIEW**: CLOSE is not supported; rewrite manually  
  `CLOSE c`
- **REVIEW**: DEALLOCATE is not supported; rewrite manually  
  `DEALLOCATE c`

## [REVIEW] dbo.probe_101_sp_executesql  (salesdb/probes/probe_101_sp_executesql.sql)  - procedure
- Output: `salesdb/probes/probe_101_sp_executesql.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_101_sp_executesql`
- **REVIEW**: sp_executesql converted to EXECUTE IMMEDIATE ... USING; check the result  
  `EXEC sp_executesql N'SELECT * FROM dbo.T WHERE Id = @id', N'@id INT', @id = 1`

## [REVIEW] dbo.probe_102_exec_concat  (salesdb/probes/probe_102_exec_concat.sql)  - procedure
- Output: `salesdb/probes/probe_102_exec_concat.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_102_exec_concat`
- **REVIEW**: dynamic SQL: the string being executed is still T-SQL and must be converted too  
  `EXEC (@sql)`
- **REVIEW**: SECURITY: dynamic SQL is built by string concatenation (@sql). If any part comes from a parameter or table data this is an SQL injection risk: pass values with EXECUTE IMMEDIATE ... USING, and allow-list identifiers (table / column names) instead of concatenating them

## [REVIEW] dbo.probe_103_dyn_const_var  (salesdb/probes/probe_103_dyn_const_var.sql)  - procedure
- Output: `salesdb/probes/probe_103_dyn_const_var.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_103_dyn_const_var`
- **REVIEW**: sp_executesql converted to EXECUTE IMMEDIATE ... USING; check the result  
  `EXEC sp_executesql @sql, N'@id INT', @id = 1`

## [OK] dbo.probe_104_print  (salesdb/probes/probe_104_print.sql)  - procedure
- Output: `salesdb/probes/probe_104_print.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_104_print`

## [OK] dbo.probe_105_while_break  (salesdb/probes/probe_105_while_break.sql)  - procedure
- Output: `salesdb/probes/probe_105_while_break.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_105_while_break`

## [OK] dbo.probe_106_if_exists  (salesdb/probes/probe_106_if_exists.sql)  - procedure
- Output: `salesdb/probes/probe_106_if_exists.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_106_if_exists`

## [OK] dbo.probe_107_case_expr  (salesdb/probes/probe_107_case_expr.sql)  - procedure
- Output: `salesdb/probes/probe_107_case_expr.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_107_case_expr`

## [OK] dbo.probe_108_declare_mid  (salesdb/probes/probe_108_declare_mid.sql)  - procedure
- Output: `salesdb/probes/probe_108_declare_mid.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_108_declare_mid`

## [OK] dbo.probe_109_select_assign  (salesdb/probes/probe_109_select_assign.sql)  - procedure
- Output: `salesdb/probes/probe_109_select_assign.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_109_select_assign`
- **note**: SELECT @var = ... FROM ... assigns the LAST row in T-SQL; BigQuery version uses LIMIT 1 (add ORDER BY if the row matters). If NO row matches, T-SQL keeps the old value but BigQuery sets the variable to NULL

## [REVIEW] dbo.probe_110_exec_output  (salesdb/probes/probe_110_exec_output.sql)  - procedure
- Output: `salesdb/probes/probe_110_exec_output.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_110_exec_output`
- **REVIEW**: named arguments in call to usp_Other: callee not found, check argument order  
  `EXEC dbo.usp_Other @In = 1, @Out = @r OUTPUT`

## [OK] dbo.probe_111_default_params  (salesdb/probes/probe_111_default_params.sql)  - procedure
- Output: `salesdb/probes/probe_111_default_params.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_111_default_params`

## [OK] dbo.probe_112_return_code  (salesdb/probes/probe_112_return_code.sql)  - procedure
- Output: `salesdb/probes/probe_112_return_code.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_112_return_code`
- **note**: RETURN value is discarded (BigQuery procedures return no status code; set config 'return_status_mode': 'out_param' to keep it in an OUT parameter)

## [OK] dbo.probe_113_multi_resultset  (salesdb/probes/probe_113_multi_resultset.sql)  - procedure
- Output: `salesdb/probes/probe_113_multi_resultset.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_113_multi_resultset`

## [REVIEW] dbo.probe_114_tvp  (salesdb/probes/probe_114_tvp.sql)  - procedure
- Output: `salesdb/probes/probe_114_tvp.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_114_tvp`
- **REVIEW**: table-valued parameter @items: type dbo.ItemList is unknown - add its CREATE TYPE ... AS TABLE file to the input folder or config 'table_types' so it becomes ARRAY<STRUCT<...>>  
  `dbo.ItemList`

## [OK] dbo.probe_115_with_options  (salesdb/probes/probe_115_with_options.sql)  - procedure
- Output: `salesdb/probes/probe_115_with_options.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_115_with_options`
- **note**: procedure options dropped: WITH EXECUTE AS OWNER, RECOMPILE

## [OK] dbo.probe_116_sp_rename  (salesdb/probes/probe_116_sp_rename.sql)  - procedure
- Output: `salesdb/probes/probe_116_sp_rename.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_116_sp_rename`
- **note**: sp_rename (COLUMN) -> ALTER TABLE ... RENAME COLUMN (DDL: not allowed inside a transaction)

## [OK] dbo.probe_117_output_param  (salesdb/probes/probe_117_output_param.sql)  - procedure
- Output: `salesdb/probes/probe_117_output_param.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_117_output_param`

## [OK] dbo.probe_118_bit_compare  (salesdb/probes/probe_118_bit_compare.sql)  - procedure
- Output: `salesdb/probes/probe_118_bit_compare.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_118_bit_compare`

## [OK] dbo.probe_119_money  (salesdb/probes/probe_119_money.sql)  - procedure
- Output: `salesdb/probes/probe_119_money.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_119_money`

## [OK] dbo.probe_120_guid_type  (salesdb/probes/probe_120_guid_type.sql)  - procedure
- Output: `salesdb/probes/probe_120_guid_type.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_120_guid_type`

## [REVIEW] dbo.probe_121_xml_type  (salesdb/probes/probe_121_xml_type.sql)  - procedure
- Output: `salesdb/probes/probe_121_xml_type.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_121_xml_type`
- **REVIEW**: variable @x: XML mapped to STRING

## [REVIEW] dbo.probe_122_hierarchyid  (salesdb/probes/probe_122_hierarchyid.sql)  - procedure
- Output: `salesdb/probes/probe_122_hierarchyid.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_122_hierarchyid`
- **REVIEW**: variable @h: user-defined or unsupported type 'HIERARCHYID' mapped to STRING

## [REVIEW] dbo.probe_123_sql_variant  (salesdb/probes/probe_123_sql_variant.sql)  - procedure
- Output: `salesdb/probes/probe_123_sql_variant.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_123_sql_variant`
- **REVIEW**: variable @v: user-defined or unsupported type 'SQL_VARIANT' mapped to STRING

## [REVIEW] dbo.probe_124_rowversion  (salesdb/probes/probe_124_rowversion.sql)  - procedure
- Output: `salesdb/probes/probe_124_rowversion.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_124_rowversion`
- **REVIEW**: column Ver: rowversion has no equivalent; mapped to BYTES

## [OK] dbo.probe_125_geography  (salesdb/probes/probe_125_geography.sql)  - procedure
- Output: `salesdb/probes/probe_125_geography.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_125_geography`

## [OK] dbo.probe_126_datetime_types  (salesdb/probes/probe_126_datetime_types.sql)  - procedure
- Output: `salesdb/probes/probe_126_datetime_types.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_126_datetime_types`

## [OK] dbo.probe_127_binary_types  (salesdb/probes/probe_127_binary_types.sql)  - procedure
- Output: `salesdb/probes/probe_127_binary_types.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_127_binary_types`

## [OK] dbo.probe_128_nvarchar_max  (salesdb/probes/probe_128_nvarchar_max.sql)  - procedure
- Output: `salesdb/probes/probe_128_nvarchar_max.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_128_nvarchar_max`

## [OK] dbo.probe_129_temp_table  (salesdb/probes/probe_129_temp_table.sql)  - procedure
- Output: `salesdb/probes/probe_129_temp_table.sql`  ->  `resource1.sql_server_centralasia_salesdb.probe_129_temp_table`
