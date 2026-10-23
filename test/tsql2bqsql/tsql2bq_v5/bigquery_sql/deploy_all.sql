-- All converted objects in dependency order (views first, called procedures before callers). Review before running.

-- Source: dbo.probe_001_scope_identity (SQL Server stored procedure)
-- Probe 001: SCOPE_IDENTITY()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_001_scope_identity`()
BEGIN
  DECLARE v_id INT64;
  INSERT INTO `resource1.sql_server_centralasia_salesdb.T` (
    Name
  )
  VALUES
    ('a');
  -- TODO(review): SCOPE_IDENTITY: no identity columns in BigQuery; generate keys explicitly (e.g. GENERATE_UUID())
  SET v_id = SCOPE_IDENTITY();
  SELECT
    v_id AS NewId;
END;

-- Source: dbo.probe_002_at_identity (SQL Server stored procedure)
-- Probe 002: @@IDENTITY
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_002_at_identity`()
BEGIN
  DECLARE v_id INT64;
  -- TODO(review): @@IDENTITY: no identity columns in BigQuery
  SET v_id = @@IDENTITY;
  SELECT
    v_id AS i;
END;

-- Source: dbo.probe_003_ident_current (SQL Server stored procedure)
-- Probe 003: IDENT_CURRENT()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_003_ident_current`()
BEGIN
  -- TODO(review): IDENT_CURRENT: no identity columns in BigQuery
  SELECT
    IDENT_CURRENT('dbo.T') AS i;
END;

-- Source: dbo.probe_004_identity_column (SQL Server stored procedure)
-- Probe 004: IDENTITY(1,1) column
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_004_identity_column`()
BEGIN
  -- TODO(review): IDENTITY on column Id dropped; BigQuery has no identity columns
  CREATE OR REPLACE TEMP TABLE tmp_x (
    Id INT64,
    Name STRING(10)
  );
  INSERT INTO tmp_x (
    Name
  )
  VALUES
    ('a');
  SELECT
    *
  FROM tmp_x;
END;

-- Source: dbo.probe_005_sequence (SQL Server stored procedure)
-- Probe 005: NEXT VALUE FOR (SEQUENCE)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_005_sequence`()
BEGIN
  -- TODO(review): NEXT VALUE FOR <sequence>: BigQuery has no sequences; use GENERATE_UUID() or ROW_NUMBER() + MAX(existing key)
  SELECT
    NEXT VALUE FOR dbo.Seq1 AS n;
END;

-- Source: dbo.probe_006_newid (SQL Server stored procedure)
-- Probe 006: NEWID()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_006_newid`()
BEGIN
  SELECT
    GENERATE_UUID() AS g;
END;

-- Source: dbo.probe_007_error_number (SQL Server stored procedure)
-- Probe 007: ERROR_NUMBER()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_007_error_number`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    -- NOTE: ERROR_NUMBER() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)
    SELECT
      CAST(NULL AS INT64) AS n;
  END;
END;

-- Source: dbo.probe_008_error_sev_state (SQL Server stored procedure)
-- Probe 008: ERROR_SEVERITY() / ERROR_STATE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_008_error_sev_state`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    -- NOTE: ERROR_SEVERITY() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)
    -- NOTE: ERROR_STATE() has no BigQuery equivalent: returns NULL (the error text is in @@error.message, location in @@error.stack_trace)
    SELECT
      CAST(NULL AS INT64) AS a,
      CAST(NULL AS INT64) AS b;
  END;
END;

-- Source: dbo.probe_009_error_line_proc (SQL Server stored procedure)
-- Probe 009: ERROR_LINE() / ERROR_PROCEDURE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_009_error_line_proc`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    -- NOTE: ERROR_LINE() -> @@error.stack_trace[SAFE_OFFSET(0)].line (a line number of the BigQuery script, not of the original T-SQL)
    -- NOTE: ERROR_PROCEDURE() replaced by the procedure name 'probe_009_error_line_proc' (BigQuery has no equivalent; if the error came from a called procedure, the call stack is in @@error.stack_trace)
    SELECT
      @@error.stack_trace[SAFE_OFFSET(0)].line AS a,
      'probe_009_error_line_proc' AS b;
  END;
END;

-- Source: dbo.probe_010_error_message (SQL Server stored procedure)
-- Probe 010: ERROR_MESSAGE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_010_error_message`()
BEGIN
  BEGIN
    SELECT
      DIV(1, 0);
  EXCEPTION WHEN ERROR THEN
    SELECT
      @@error.message AS m;
  END;
END;

-- Source: dbo.probe_011_at_error (SQL Server stored procedure)
-- Probe 011: @@ERROR
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_011_at_error`()
BEGIN
  -- NOTE: IF @@ERROR check converted to an exception handler
  BEGIN
    UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
    WHERE
      Id = 1;
  EXCEPTION WHEN ERROR THEN
    SELECT
      1 AS failed;
  END;
END;

-- Source: dbo.probe_012_raiserror (SQL Server stored procedure)
-- Probe 012: RAISERROR(...)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_012_raiserror`()
BEGIN
  -- NOTE: RAISERROR outside TRY: T-SQL CONTINUES with the next statement, BigQuery RAISE stops the procedure (usually what was intended - check if code follows that should still run)
  RAISE USING MESSAGE = FORMAT('Bad value %d', 5);
END;

-- Source: dbo.probe_013_throw_number (SQL Server stored procedure)
-- Probe 013: THROW <number>, <msg>, <state>
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_013_throw_number`()
BEGIN
  RAISE USING MESSAGE = 'custom error';
END;

-- Source: dbo.probe_014_xact_state (SQL Server stored procedure)
-- Probe 014: XACT_STATE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_014_xact_state`()
BEGIN
  DECLARE v_in_transaction BOOL DEFAULT FALSE;
  BEGIN
    BEGIN TRANSACTION;
    SET v_in_transaction = TRUE;
    UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
    WHERE
      TRUE;
    COMMIT TRANSACTION;
    SET v_in_transaction = FALSE;
  EXCEPTION WHEN ERROR THEN
    IF (CASE WHEN v_in_transaction THEN -1 ELSE 0 END) = -1 THEN
      ROLLBACK TRANSACTION;
      SET v_in_transaction = FALSE;
    END IF;
  END;
END;

-- Source: dbo.probe_015_trancount (SQL Server stored procedure)
-- Probe 015: @@TRANCOUNT guard
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_015_trancount`()
BEGIN
  DECLARE v_in_transaction BOOL DEFAULT FALSE;
  BEGIN
    BEGIN TRANSACTION;
    SET v_in_transaction = TRUE;
    UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
    WHERE
      TRUE;
    COMMIT TRANSACTION;
    SET v_in_transaction = FALSE;
  EXCEPTION WHEN ERROR THEN
    IF v_in_transaction THEN
      ROLLBACK TRANSACTION;
      SET v_in_transaction = FALSE;
    END IF;
  END;
END;

-- Source: dbo.probe_016_save_transaction (SQL Server stored procedure)
-- Probe 016: SAVE TRANSACTION (savepoint)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_016_save_transaction`()
BEGIN
  BEGIN TRANSACTION;
  -- TODO(review): savepoints are not supported in BigQuery
  -- TODO(manual): savepoints are not supported
  --   SAVE TRANSACTION sp1
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    TRUE;
  -- TODO(review): ROLLBACK to a savepoint: BigQuery has no savepoints - this would roll back the WHOLE transaction
  -- TODO(manual): rollback to savepoint is not supported
  --   ROLLBACK TRANSACTION sp1
  COMMIT TRANSACTION;
END;

-- Source: dbo.probe_017_nested_tran (SQL Server stored procedure)
-- Probe 017: Nested BEGIN TRAN
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_017_nested_tran`()
BEGIN
  BEGIN TRANSACTION;
  -- NOTE: nested BEGIN TRANSACTION removed: T-SQL only increments @@TRANCOUNT, BigQuery does not allow nested transactions (the matching inner COMMIT is removed too)
  -- [removed] nested BEGIN TRANSACTION
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    TRUE;
  -- [removed] inner COMMIT TRANSACTION (T-SQL only decrements @@TRANCOUNT)
  COMMIT TRANSACTION;
END;

-- Source: dbo.probe_018_xact_abort (SQL Server stored procedure)
-- Probe 018: SET XACT_ABORT ON
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_018_xact_abort`()
BEGIN
  -- [removed] SET XACT_ABORT ON
  SELECT
    1 AS a;
END;

-- Source: dbo.probe_019_transaction_basic (SQL Server stored procedure)
-- Probe 019: BEGIN/COMMIT TRANSACTION
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_019_transaction_basic`()
BEGIN
  BEGIN TRANSACTION;
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    Id = 1;
  COMMIT TRANSACTION;
END;

-- Source: dbo.probe_020_object_id (SQL Server stored procedure)
-- Probe 020: OBJECT_ID()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_020_object_id`()
BEGIN
  -- NOTE: OBJECT_ID('dbo.T') IS [NOT] NULL -> [NOT] EXISTS on sql_server_centralasia_salesdb.INFORMATION_SCHEMA.TABLES
  IF EXISTS(SELECT 1 FROM `resource1.sql_server_centralasia_salesdb.INFORMATION_SCHEMA.TABLES` WHERE table_name = 'T') THEN
    SELECT
      1 AS e;
  END IF;
END;

-- Source: dbo.probe_021_db_name (SQL Server stored procedure)
-- Probe 021: DB_NAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_021_db_name`()
BEGIN
  -- NOTE: DB_NAME() replaced by the target dataset name 'sql_server_centralasia_salesdb'
  SELECT
    'sql_server_centralasia_salesdb' AS d;
END;

-- Source: dbo.probe_022_host_app (SQL Server stored procedure)
-- Probe 022: HOST_NAME() / APP_NAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_022_host_app`()
BEGIN
  -- TODO(review): HOST_NAME: no equivalent
  -- TODO(review): APP_NAME: no equivalent
  SELECT
    HOST_NAME() AS h,
    APP_NAME() AS a;
END;

-- Source: dbo.probe_023_user_fn (SQL Server stored procedure)
-- Probe 023: SUSER_SNAME() / SYSTEM_USER / USER_NAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_023_user_fn`()
BEGIN
  -- NOTE: SUSER_SNAME() / SYSTEM_USER / CURRENT_USER -> SESSION_USER(): returns the Google account e-mail (user or service account), not the SQL Server login
  SELECT
    SESSION_USER() AS a,
    SESSION_USER() AS b,
    SESSION_USER() AS c;
END;

-- Source: dbo.probe_024_server_vars (SQL Server stored procedure)
-- Probe 024: @@SPID / @@SERVERNAME / @@VERSION
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_024_server_vars`()
BEGIN
  -- TODO(review): @@SPID: no equivalent
  -- TODO(review): @@SERVERNAME: no equivalent
  -- TODO(review): @@VERSION: no equivalent
  SELECT
    @@SPID AS a,
    @@SERVERNAME AS b,
    @@VERSION AS c;
END;

-- Source: dbo.probe_025_rowcount (SQL Server stored procedure)
-- Probe 025: @@ROWCOUNT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_025_rowcount`()
BEGIN
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    TRUE;
  SELECT
    @@row_count AS n;
END;

-- Source: dbo.probe_026_session_context (SQL Server stored procedure)
-- Probe 026: SESSION_CONTEXT()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_026_session_context`()
BEGIN
  -- TODO(review): SESSION_CONTEXT() does not exist in BigQuery: no equivalent
  SELECT
    SESSION_CONTEXT('k') AS v;
END;

-- Source: dbo.probe_027_set_options (SQL Server stored procedure)
-- Probe 027: SET NOCOUNT / ANSI_NULLS / QUOTED_IDENTIFIER
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_027_set_options`()
BEGIN
  -- [removed] SET NOCOUNT ON
  -- [removed] SET ANSI_NULLS ON
  -- [removed] SET QUOTED_IDENTIFIER ON
  SELECT
    1 AS a;
END;

-- Source: dbo.probe_028_stuff (SQL Server stored procedure)
-- Probe 028: STUFF()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_028_stuff`()
BEGIN
  -- NOTE: STUFF(s, start, len, rep) -> SUBSTR(s, 1, start-1) || rep || SUBSTR(s, start+len) (T-SQL returns NULL when start is 0 or beyond the string; this version does not)
  SELECT
    (
      SUBSTRING('abcdef', 1, 2 - 1) || 'X' || SUBSTRING('abcdef', 2 + 3)
    ) AS s;
END;

-- Source: dbo.probe_029_quotename (SQL Server stored procedure)
-- Probe 029: QUOTENAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_029_quotename`()
BEGIN
  -- TODO(review): QUOTENAME: no equivalent
  SELECT
    QUOTENAME('a b') AS q;
END;

-- Source: dbo.probe_030_parsename (SQL Server stored procedure)
-- Probe 030: PARSENAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_030_parsename`()
BEGIN
  SELECT
    'c' AS p;
END;

-- Source: dbo.probe_031_formatmessage (SQL Server stored procedure)
-- Probe 031: FORMATMESSAGE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_031_formatmessage`()
BEGIN
  -- NOTE: FORMATMESSAGE -> FORMAT (printf-style; %i/%u became %d)
  SELECT
    FORMAT('x %d', 5) AS m;
END;

-- Source: dbo.probe_032_patindex (SQL Server stored procedure)
-- Probe 032: PATINDEX()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_032_patindex`()
BEGIN
  -- NOTE: PATINDEX('%b%', x) -> REGEXP_INSTR(x, '(?s)b') (case-sensitive; SQL Server's default collation is not - set case_sensitivity 'lower' to add (?i))
  SELECT
    REGEXP_INSTR(Name, '(?s)b') AS p
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_033_string_split (SQL Server stored procedure)
-- Probe 033: STRING_SPLIT()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_033_string_split`()
BEGIN
  -- NOTE: STRING_SPLIT(s, sep) -> UNNEST(SPLIT(s, sep)) AS value (same rows; the order of the values is not guaranteed in either engine unless enable_ordinal / WITH OFFSET is used)
  SELECT
    value
  FROM (
    SELECT
      value
    FROM UNNEST(SPLIT('a,b,c', ',')) AS value
  ) AS value_list;
END;

-- Source: dbo.probe_034_difference (SQL Server stored procedure)
-- Probe 034: DIFFERENCE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_034_difference`()
BEGIN
  -- TODO(review): DIFFERENCE(a, b) -> position-by-position SOUNDEX comparison (0-4); T-SQL's algorithm can score differently for some inputs
  SELECT
    (
      SELECT
        COUNTIF(SUBSTR(SOUNDEX('a'), i, 1) = SUBSTR(SOUNDEX('b'), i, 1))
      FROM UNNEST([1, 2, 3, 4]) AS i
    ) AS d;
END;

-- Source: dbo.probe_035_like_class (SQL Server stored procedure)
-- Probe 035: LIKE '[a-c]%' character class
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_035_like_class`()
BEGIN
  -- NOTE: LIKE '[a-c]%' uses T-SQL character classes, converted to REGEXP_CONTAINS
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    REGEXP_CONTAINS(Name, '(?s)^[a-c].*$');
END;

-- Source: dbo.probe_036_len_charindex (SQL Server stored procedure)
-- Probe 036: LEN() / CHARINDEX()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_036_len_charindex`()
BEGIN
  -- NOTE: CHARINDEX/INSTR: SQL Server's default collation finds 'A' in 'abc', BigQuery INSTR is case-sensitive (case_sensitivity 'lower' wraps both sides in LOWER())
  SELECT
    LENGTH(RTRIM(CAST(Name AS STRING), ' ')) AS l,
    INSTR(Name, 'a') AS c
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_037_replicate_space (SQL Server stored procedure)
-- Probe 037: REPLICATE() / SPACE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_037_replicate_space`()
BEGIN
  SELECT
    REPEAT('a', 3) AS r,
    REPEAT(' ', 2) AS s;
END;

-- Source: dbo.probe_038_datalength (SQL Server stored procedure)
-- Probe 038: DATALENGTH()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_038_datalength`()
BEGIN
  -- NOTE: DATALENGTH -> BYTE_LENGTH (T-SQL counts 2 bytes per NVARCHAR character, BigQuery counts UTF-8 bytes)
  SELECT
    BYTE_LENGTH(Name) AS d
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_039_format_fn (SQL Server stored procedure)
-- Probe 039: FORMAT(date, 'yyyy-MM-dd')
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_039_format_fn`()
BEGIN
  SELECT
    FORMAT_DATETIME('%Y-%m-%d', CAST(CreatedAt AS DATETIME)) AS f
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_040_string_agg (SQL Server stored procedure)
-- Probe 040: STRING_AGG()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_040_string_agg`()
BEGIN
  SELECT
    STRING_AGG(Name, ',') AS s
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_041_string_agg_wg (SQL Server stored procedure)
-- Probe 041: STRING_AGG ... WITHIN GROUP (ORDER BY)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_041_string_agg_wg`()
BEGIN
  SELECT
    STRING_AGG(Name, ','
    ORDER BY
      Name) AS s
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_042_char_unicode (SQL Server stored procedure)
-- Probe 042: CHAR() / NCHAR() / UNICODE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_042_char_unicode`()
BEGIN
  SELECT
    CHR(  65) AS c,
    UNICODE('A') AS u,
    CODE_POINTS_TO_STRING([66]) AS n;
END;

-- Source: dbo.probe_043_collate (SQL Server stored procedure)
-- Probe 043: COLLATE (case-sensitive collation)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_043_collate`()
BEGIN
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    Name = 'a';
END;

-- Source: dbo.probe_044_concat_isnull (SQL Server stored procedure)
-- Probe 044: 'a' + ISNULL(x,'')
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_044_concat_isnull`()
BEGIN
  SELECT
    'a' || COALESCE(Name, '') AS s
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_045_trim_fn (SQL Server stored procedure)
-- Probe 045: LTRIM / RTRIM / TRIM
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_045_trim_fn`()
BEGIN
  SELECT
    LTRIM(RTRIM(Name)) AS t,
    TRIM(Name) AS u
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_046_case_insensitive (SQL Server stored procedure)
-- Probe 046: Text equality (case-insensitive in SQL Server)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_046_case_insensitive`()
BEGIN
  -- TODO(review): string column Name is compared with a text literal: SQL Server's default collation is case-insensitive but BigQuery is case-sensitive, so 'NEW' will not match 'new'. Confirm the data's casing is consistent, or set config 'case_sensitivity': 'lower' to wrap both sides in LOWER()
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    Name = 'abc';
END;

-- Source: dbo.probe_047_year_month_day (SQL Server stored procedure)
-- Probe 047: YEAR() / MONTH() / DAY()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_047_year_month_day`()
BEGIN
  SELECT
    EXTRACT(YEAR FROM CreatedAt) AS y,
    EXTRACT(MONTH FROM CreatedAt) AS m,
    EXTRACT(DAY FROM CreatedAt) AS d
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_048_datepart_name (SQL Server stored procedure)
-- Probe 048: DATEPART() / DATENAME()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_048_datepart_name`()
BEGIN
  SELECT
    EXTRACT(DAYOFWEEK FROM CreatedAt) AS a,
    FORMAT_TIMESTAMP('%B', CAST(CreatedAt AS TIMESTAMP)) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_049_dateadd_diff (SQL Server stored procedure)
-- Probe 049: DATEADD() / DATEDIFF()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_049_dateadd_diff`()
BEGIN
  -- NOTE: DATEADD on an expression of unknown type: DATE_ADD only accepts DATE - if it is DATETIME/TIMESTAMP use DATETIME_ADD/TIMESTAMP_ADD (or list the column in config 'column_types')
  SELECT
    DATE_ADD(CreatedAt, INTERVAL 5 DAY) AS a,
    DATETIME_DIFF(CURRENT_DATETIME(), CAST(CreatedAt AS DATETIME), DAY) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_050_eomonth (SQL Server stored procedure)
-- Probe 050: EOMONTH() / DATEFROMPARTS()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_050_eomonth`()
BEGIN
  SELECT
    LAST_DAY(CAST(CURRENT_DATETIME() AS DATE)) AS a,
    DATE(2026, 1, 15) AS b;
END;

-- Source: dbo.probe_051_at_time_zone (SQL Server stored procedure)
-- Probe 051: AT TIME ZONE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_051_at_time_zone`()
BEGIN
  -- NOTE: x AT TIME ZONE z -> TIMESTAMP(x, z), assuming x is a DATETIME (wall-clock time in z)
  SELECT
    TIMESTAMP(CreatedAt, 'UTC') AS t
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_052_sysdatetime (SQL Server stored procedure)
-- Probe 052: SYSDATETIME() / GETUTCDATE() / SYSDATETIMEOFFSET()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_052_sysdatetime`()
BEGIN
  SELECT
    CURRENT_DATETIME() AS a,
    CURRENT_DATETIME('UTC') AS b,
    CURRENT_TIMESTAMP() AS c;
END;

-- Source: dbo.probe_053_isdate (SQL Server stored procedure)
-- Probe 053: ISDATE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_053_isdate`()
BEGIN
  -- TODO(review): ISDATE(x) -> IF(SAFE_CAST(x AS DATETIME/DATE) IS NOT NULL, 1, 0): only ISO strings count as dates in BigQuery ('Jan 1 2020', '01/02/2020' return 0 here, 1 in T-SQL)
  SELECT
    IF(NOT SAFE_CAST(Name AS DATETIME) IS NULL OR NOT SAFE_CAST(Name AS DATE) IS NULL, 1, 0) AS d
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_054_convert_style (SQL Server stored procedure)
-- Probe 054: CONVERT(VARCHAR, date, style)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_054_convert_style`()
BEGIN
  SELECT
    SUBSTRING(FORMAT_DATETIME('%Y-%m-%d %H:%M:%S', CAST(CreatedAt AS DATETIME)), 1, 10) AS a,
    SUBSTRING(FORMAT_DATETIME('%d/%m/%Y', CAST(CreatedAt AS DATETIME)), 1, 10) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_055_try_convert (SQL Server stored procedure)
-- Probe 055: TRY_CONVERT() / TRY_CAST()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_055_try_convert`()
BEGIN
  SELECT
    SAFE_CAST(Name AS INT64) AS a,
    SAFE_CAST(Name AS INT64) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_056_try_parse (SQL Server stored procedure)
-- Probe 056: TRY_PARSE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_056_try_parse`()
BEGIN
  -- TODO(review): TRY_PARSE(x AS DATE) -> SAFE_CAST: BigQuery only parses ISO formats (YYYY-MM-DD, plain numbers); T-SQL also accepts culture formats ('12/31/2020', '$1,000') - use SAFE.PARSE_DATE('%m/%d/%Y', x) for those
  SELECT
    SAFE_CAST(Name AS DATE) AS a
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_057_isnumeric (SQL Server stored procedure)
-- Probe 057: ISNUMERIC()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_057_isnumeric`()
BEGIN
  -- TODO(review): ISNUMERIC(x) -> IF(SAFE_CAST(x AS FLOAT64) IS NOT NULL, 1, 0): T-SQL also returns 1 for '$', ',', '.', '-', '1e5d' ... and 0 for 'NaN'/'inf'; check the inputs you expect
  SELECT
    IF(NOT SAFE_CAST(Name AS FLOAT64) IS NULL, 1, 0) AS n
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_058_choose_iif (SQL Server stored procedure)
-- Probe 058: CHOOSE() / IIF()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_058_choose_iif`()
BEGIN
  SELECT
    CASE 2 WHEN 1 THEN 'a' WHEN 2 THEN 'b' END AS c,
    IF(Amount > 1, 'h', 'l') AS i
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_059_hashbytes (SQL Server stored procedure)
-- Probe 059: HASHBYTES()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_059_hashbytes`()
BEGIN
  -- TODO(review): HASHBYTES: use MD5()/SHA256()/SHA512()
  SELECT
    SHA256(Name) AS h
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_060_checksum (SQL Server stored procedure)
-- Probe 060: CHECKSUM() / BINARY_CHECKSUM()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_060_checksum`()
BEGIN
  -- TODO(review): CHECKSUM: use FARM_FINGERPRINT()
  -- TODO(review): BINARY_CHECKSUM: use FARM_FINGERPRINT()
  SELECT
    CHECKSUM(Name) AS c,
    BINARY_CHECKSUM(Name) AS b
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_061_rand_seed (SQL Server stored procedure)
-- Probe 061: RAND(seed)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_061_rand_seed`()
BEGIN
  -- TODO(review): RAND(seed): BigQuery RAND() takes no seed, so the value is not repeatable
  SELECT
    RAND() AS r;
END;

-- Source: dbo.probe_062_int_division (SQL Server stored procedure)
-- Probe 062: int / int
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_062_int_division`()
BEGIN
  DECLARE v_a INT64;
  DECLARE v_b INT64;
  SET v_a = 5;
  SET v_b = 2;
  SELECT
    DIV(v_a, v_b) AS q;
END;

-- Source: dbo.probe_063_modulo_bitwise (SQL Server stored procedure)
-- Probe 063: % and & | ^ operators
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_063_modulo_bitwise`()
BEGIN
  SELECT
    MOD(7, 3) AS m,
    6 & 3 AS a,
    6 | 1 AS o,
    6 ^ 3 AS x;
END;

-- Source: dbo.probe_064_stdev_var (SQL Server stored procedure)
-- Probe 064: STDEV() / VAR()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_064_stdev_var`()
BEGIN
  SELECT
    STDDEV(Amount) AS s,
    VAR_SAMP(Amount) AS v
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_065_percentile_cont (SQL Server stored procedure)
-- Probe 065: PERCENTILE_CONT ... WITHIN GROUP
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_065_percentile_cont`()
BEGIN
  SELECT DISTINCT
    PERCENTILE_CONT(Amount, 0.5) OVER (PARTITION BY Name) AS p
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_066_rollup_grouping (SQL Server stored procedure)
-- Probe 066: GROUP BY ROLLUP + GROUPING_ID()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_066_rollup_grouping`()
BEGIN
  SELECT
    Name,
    SUM(Amount) AS s,
    GROUPING(Name) AS g
  FROM `resource1.sql_server_centralasia_salesdb.T`
  GROUP BY
    ROLLUP (
      Name
    );
END;

-- Source: dbo.probe_067_top_isnull_getdate (SQL Server stored procedure)
-- Probe 067: TOP n / ISNULL / GETDATE (baseline)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_067_top_isnull_getdate`()
BEGIN
  SELECT
    Id,
    COALESCE(Name, 'x') AS n,
    CURRENT_DATETIME() AS d
  FROM `resource1.sql_server_centralasia_salesdb.T`
  ORDER BY
    Id
  LIMIT 5;
END;

-- Source: dbo.probe_068_cross_apply (SQL Server stored procedure)
-- Probe 068: CROSS APPLY
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_068_cross_apply`()
BEGIN
  -- NOTE: CROSS APPLY -> CROSS JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) AS x (correlated; dry-run it - BigQuery rejects correlated subqueries it cannot de-correlate)
  SELECT
    c.Id,
    x.Total
  FROM `resource1.sql_server_centralasia_salesdb.T` AS c
  CROSS JOIN UNNEST(ARRAY(
    SELECT AS STRUCT
      SUM(Amount) AS Total
    FROM `resource1.sql_server_centralasia_salesdb.T2` AS o
    WHERE
      o.Id = c.Id
  )) AS x;
END;

-- Source: dbo.probe_069_outer_apply (SQL Server stored procedure)
-- Probe 069: OUTER APPLY
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_069_outer_apply`()
BEGIN
  -- NOTE: OUTER APPLY -> LEFT JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) AS x (correlated; dry-run it - BigQuery rejects correlated subqueries it cannot de-correlate)
  SELECT
    c.Id,
    x.Total
  FROM `resource1.sql_server_centralasia_salesdb.T` AS c
  LEFT JOIN UNNEST(ARRAY(
    SELECT AS STRUCT
      SUM(Amount) AS Total
    FROM `resource1.sql_server_centralasia_salesdb.T2` AS o
    WHERE
      o.Id = c.Id
  )) AS x
    ON TRUE;
END;

-- Source: dbo.probe_070_output_clause (SQL Server stored procedure)
-- Probe 070: OUTPUT clause (UPDATE ... OUTPUT)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_070_output_clause`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_chg (
    Id INT64
  );
  -- TODO(review): OUTPUT: OUTPUT clause is not supported
  UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
  WHERE
    Id = 1
  RETURNING inserted.Id;
END;

-- Source: dbo.probe_071_pivot (SQL Server stored procedure)
-- Probe 071: PIVOT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_071_pivot`()
BEGIN
  -- NOTE: PIVOT: converted to BigQuery PIVOT syntax; compare the result columns once
  SELECT
    *
  FROM (
    SELECT
      Name,
      Amount
    FROM `resource1.sql_server_centralasia_salesdb.T`
  ) AS s
  PIVOT(SUM(Amount) FOR Name IN ('a' AS a, 'b' AS b)) AS p;
END;

-- Source: dbo.probe_072_unpivot (SQL Server stored procedure)
-- Probe 072: UNPIVOT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_072_unpivot`()
BEGIN
  -- NOTE: UNPIVOT: converted to BigQuery UNPIVOT syntax; compare the result columns once
  SELECT
    *
  FROM `resource1.sql_server_centralasia_salesdb.T`
  UNPIVOT(Val FOR Col IN (A, B)) AS u;
END;

-- Source: dbo.probe_073_top_ties (SQL Server stored procedure)
-- Probe 073: TOP n WITH TIES
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_073_top_ties`()
BEGIN
  -- NOTE: TOP n WITH TIES converted to QUALIFY RANK() OVER (ORDER BY ...) <= n
  SELECT
    Id,
    Amount
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    TRUE
  QUALIFY
    RANK() OVER (ORDER BY Amount DESC) <= 3
  ORDER BY
    Amount DESC;
END;

-- Source: dbo.probe_074_top_percent (SQL Server stored procedure)
-- Probe 074: TOP n PERCENT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_074_top_percent`()
BEGIN
  -- NOTE: TOP n PERCENT converted to QUALIFY ROW_NUMBER() OVER (ORDER BY ...) <= CEIL(COUNT(*) OVER () * p / 100)
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    TRUE
  QUALIFY
    ROW_NUMBER() OVER (ORDER BY Id) <= CEIL(COUNT(*) OVER () * 10 / 100.0)
  ORDER BY
    Id;
END;

-- Source: dbo.probe_075_for_xml_path (SQL Server stored procedure)
-- Probe 075: STUFF((... FOR XML PATH('')), 1, 1, '')
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_075_for_xml_path`()
BEGIN
  DECLARE v_s STRING;
  -- NOTE: STUFF((SELECT sep + x ... FOR XML PATH('')), 1, n, '') converted to STRING_AGG (note: FOR XML also escaped &, <, > as &amp; &lt; &gt; - STRING_AGG does not)
  SET v_s = (SELECT STRING_AGG(Name, ',') FROM `resource1.sql_server_centralasia_salesdb.T`);
END;

-- Source: dbo.probe_076_xml_value (SQL Server stored procedure)
-- Probe 076: XML .value() / .query() / .nodes()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_076_xml_value`()
BEGIN
  DECLARE v_x STRING;
  -- TODO(review): variable @x: XML mapped to STRING
  SET v_x = '<a><b>1</b></a>';
  SELECT
    v_x.value('(/a/b)[1]', 'INT') AS v;
END;

-- Source: dbo.probe_077_for_json (SQL Server stored procedure)
-- Probe 077: FOR JSON PATH
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_077_for_json`()
BEGIN
  -- NOTE: FOR JSON PATH -> TO_JSON_STRING(ARRAY(SELECT AS STRUCT ...)) - NULL columns appear as "col":null in BigQuery but are omitted by FOR JSON (add INCLUDE_NULL_VALUES semantics or strip them if consumers care)
  SELECT
    NULLIF(
      TO_JSON_STRING(
        ARRAY(SELECT AS STRUCT
          Id,
          Name
        FROM `resource1.sql_server_centralasia_salesdb.T`)
      ),
      '[]'
    ) AS json;
END;

-- Source: dbo.probe_078_openjson (SQL Server stored procedure)
-- Probe 078: OPENJSON()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_078_openjson`()
BEGIN
  -- TODO(review): OPENJSON: use JSON functions / UNNEST(JSON_QUERY_ARRAY(...))
  SELECT
    `key`,
    value
  FROM OPENJSON('{"a":1}');
END;

-- Source: dbo.probe_079_json_value (SQL Server stored procedure)
-- Probe 079: JSON_VALUE()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_079_json_value`()
BEGIN
  SELECT
    JSON_EXTRACT_SCALAR('{"a":1}', '$.a') AS v;
END;

-- Source: dbo.probe_080_openquery (SQL Server stored procedure)
-- Probe 080: OPENQUERY / linked server
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_080_openquery`()
BEGIN
  -- TODO(review): OPENQUERY: linked servers not supported
  SELECT
    *
  FROM OPENQUERY(LinkedSrv, 'SELECT 1 AS a');
END;

-- Source: dbo.probe_081_four_part (SQL Server stored procedure)
-- Probe 081: Four-part name (linked server)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_081_four_part`()
BEGIN
  -- TODO(review): four-part name LinkedSrv.OtherDb.dbo.T: linked servers are not reachable from BigQuery (load the data or use a federated/external table)
  SELECT
    *
  FROM LinkedSrv.OtherDb.dbo.T;
END;

-- Source: dbo.probe_082_nolock (SQL Server stored procedure)
-- Probe 082: WITH (NOLOCK) table hint
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_082_nolock`()
BEGIN
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    Id = 1;
END;

-- Source: dbo.probe_083_option_recompile (SQL Server stored procedure)
-- Probe 083: OPTION (RECOMPILE) query hint
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_083_option_recompile`()
BEGIN
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_084_insert_exec (SQL Server stored procedure)
-- Probe 084: INSERT ... EXEC proc
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_084_insert_exec`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_r (
    a INT64
  );
  -- TODO(review): INSERT ... EXEC proc: BigQuery cannot insert a procedure's result set - let the called procedure write into a TEMP TABLE (temp tables are visible to the caller in the same script) or turn it into a TABLE FUNCTION and INSERT ... SELECT from it
  -- TODO(manual): INSERT ... EXEC is not supported
  --   INSERT INTO #r EXEC dbo.usp_Other
END;

-- Source: dbo.probe_085_select_into (SQL Server stored procedure)
-- Probe 085: SELECT ... INTO #t
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_085_select_into`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_copy AS SELECT
    Id,
    Name
  FROM `resource1.sql_server_centralasia_salesdb.T`;
  SELECT
    *
  FROM tmp_copy;
END;

-- Source: dbo.probe_086_recursive_cte (SQL Server stored procedure)
-- Probe 086: Recursive CTE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_086_recursive_cte`()
BEGIN
  -- NOTE: recursive CTE n: WITH RECURSIVE added (BigQuery stops after 500 iterations; T-SQL's default MAXRECURSION is 100, OPTION (MAXRECURSION 0) = no limit)
  WITH RECURSIVE n AS (
    SELECT
      1 AS i
    UNION ALL
    SELECT
      i + 1
    FROM n
    WHERE
      i < 5
  )
  SELECT
    i
  FROM n;
END;

-- Source: dbo.probe_087_merge_basic (SQL Server stored procedure)
-- Probe 087: MERGE (basic)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_087_merge_basic`()
BEGIN
  MERGE INTO `resource1.sql_server_centralasia_salesdb.T` AS t
  USING `resource1.sql_server_centralasia_salesdb.T2` AS s
  ON t.Id = s.Id
  WHEN MATCHED THEN UPDATE SET
    Name = s.Name
  WHEN NOT MATCHED THEN INSERT (Id, Name) VALUES (s.Id, s.Name);
END;

-- Source: dbo.probe_088_merge_output (SQL Server stored procedure)
-- Probe 088: MERGE ... OUTPUT $action
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_088_merge_output`()
BEGIN
  -- TODO(review): OUTPUT: OUTPUT clause is not supported
  MERGE INTO `resource1.sql_server_centralasia_salesdb.T` AS t
  USING (
    SELECT
      1 AS Id
  ) AS s
  ON t.Id = s.Id
  WHEN MATCHED THEN UPDATE SET
    Name = 'x'
  RETURNING $action, inserted.Id;
END;

-- Source: dbo.probe_089_goto (SQL Server stored procedure)
-- Probe 089: GOTO / labels
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_089_goto`()
BEGIN
  DECLARE v_i INT64;
  SET v_i = 0;
  -- TODO(review): label lbl: BigQuery has no GOTO/labels; restructure with IF / LOOP / BREAK
  -- TODO(manual): label (GOTO target) is not supported
  --   lbl:
  SET v_i = v_i + 1;
  IF v_i < 3 THEN
    -- TODO(review): GOTO is not supported; rewrite manually
    -- TODO(manual): GOTO is not supported in BigQuery
    --   GOTO lbl
    ASSERT TRUE;  -- no-op placeholder: branch had no executable statement
  END IF;
  SELECT
    v_i AS i;
END;

-- Source: dbo.probe_090_waitfor (SQL Server stored procedure)
-- Probe 090: WAITFOR DELAY
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_090_waitfor`()
BEGIN
  -- TODO(review): WAITFOR is not supported; rewrite manually
  -- TODO(manual): WAITFOR is not supported in BigQuery
  --   WAITFOR DELAY '00:00:01'
END;

-- Source: dbo.probe_091_fulltext (SQL Server stored procedure)
-- Probe 091: Full-text CONTAINS()
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_091_fulltext`()
BEGIN
  -- NOTE: full-text CONTAINS -> CONTAINS_SUBSTR (case-insensitive SUBSTRING match, not word match: 'cat' also matches 'concatenate'); consider SEARCH()
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    CONTAINS_SUBSTR(Name, 'abc');
END;

-- Source: dbo.probe_092_table_variable (SQL Server stored procedure)
-- Probe 092: DECLARE @t TABLE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_092_table_variable`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_t (
    Id INT64,
    Name STRING(10)
  );
  INSERT INTO tmp_t
  SELECT
    Id,
    Name
  FROM `resource1.sql_server_centralasia_salesdb.T`;
  SELECT
    *
  FROM tmp_t;
END;

-- Source: dbo.probe_093_global_temp (SQL Server stored procedure)
-- Probe 093: ##global temp table
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_093_global_temp`()
BEGIN
  -- NOTE: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
  CREATE OR REPLACE TEMP TABLE tmp_g (
    Id INT64
  );
  -- NOTE: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
  INSERT INTO tmp_g
  VALUES
    (1);
  -- NOTE: global temp table ##g becomes a script-local TEMP TABLE: other sessions cannot see it in BigQuery
  SELECT
    *
  FROM tmp_g;
END;

-- Source: dbo.probe_094_update_join (SQL Server stored procedure)
-- Probe 094: UPDATE ... FROM ... JOIN
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_094_update_join`()
BEGIN
  -- NOTE: UPDATE ... FROM: BigQuery raises an error if a target row matches more than one source row (T-SQL silently uses one of them) - make sure the joined source is unique on the join key (aggregate it first if needed)
  UPDATE `resource1.sql_server_centralasia_salesdb.T` AS t SET Name = 'x'
  FROM `resource1.sql_server_centralasia_salesdb.T2` AS u
  WHERE
    t.Id = u.Id;
END;

-- Source: dbo.probe_095_delete_join (SQL Server stored procedure)
-- Probe 095: DELETE t FROM ... JOIN
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_095_delete_join`()
BEGIN
  DELETE FROM `resource1.sql_server_centralasia_salesdb.T` AS t
  WHERE
    EXISTS(
      SELECT
        1
      FROM `resource1.sql_server_centralasia_salesdb.T2` AS u
      WHERE
        t.Id = u.Id
    );
END;

-- Source: dbo.probe_096_update_top (SQL Server stored procedure)
-- Probe 096: UPDATE TOP (n)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_096_update_top`()
BEGIN
  -- TODO(review): UPDATE TOP (n) has no BigQuery equivalent: rewrite as UPDATE ... WHERE key IN (SELECT key FROM ... ORDER BY ... LIMIT n)
  -- TODO(manual): UPDATE TOP (n) is not supported
  --   UPDATE TOP (10) dbo.T SET Name = 'x'
END;

-- Source: dbo.probe_097_delete_top (SQL Server stored procedure)
-- Probe 097: DELETE TOP (n)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_097_delete_top`()
BEGIN
  -- TODO(review): DELETE TOP (n) has no BigQuery equivalent: rewrite as DELETE ... WHERE key IN (SELECT key FROM ... ORDER BY ... LIMIT n)
  -- TODO(manual): DELETE TOP (n) is not supported
  --   DELETE TOP (10) FROM dbo.T WHERE Id > 0
END;

-- Source: dbo.probe_098_identity_insert (SQL Server stored procedure)
-- Probe 098: SET IDENTITY_INSERT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_098_identity_insert`()
BEGIN
  -- NOTE: SET IDENTITY_INSERT removed (BigQuery has no identity columns; explicit values are always accepted)
  -- [removed] SET IDENTITY_INSERT dbo.T ON
END;

-- Source: dbo.probe_099_cursor_basic (SQL Server stored procedure)
-- Probe 099: CURSOR (forward-only) + @@FETCH_STATUS
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_099_cursor_basic`()
BEGIN
  DECLARE v_i INT64;
  -- [cursor -> FOR loop] DECLARE c CURSOR FOR SELECT Id FROM dbo.T
  -- [cursor -> FOR loop] OPEN c
  -- [cursor -> FOR loop] FETCH NEXT FROM c INTO @i
  -- NOTE: cursor c converted to FOR loop (BigQuery has no cursors)
  FOR rec_c IN (
    SELECT
      Id
    FROM `resource1.sql_server_centralasia_salesdb.T`
  ) DO
    SET v_i = rec_c.Id;
    UPDATE `resource1.sql_server_centralasia_salesdb.T` SET Name = 'x'
    WHERE
      Id = v_i;
  END FOR;
  -- [cursor -> FOR loop] CLOSE c
  -- [cursor -> FOR loop] DEALLOCATE c
END;

-- Source: dbo.probe_100_cursor_scroll (SQL Server stored procedure)
-- Probe 100: CURSOR SCROLL (FETCH PRIOR / LAST)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_100_cursor_scroll`()
BEGIN
  DECLARE v_i INT64;
  -- TODO(review): cursors are not supported; rewrite as FOR record IN (SELECT ...) DO ... END FOR
  -- TODO(manual): cursor declaration
  --   DECLARE c CURSOR SCROLL FOR SELECT Id FROM dbo.T
  -- TODO(review): OPEN is not supported; rewrite manually
  -- TODO(manual): OPEN is not supported in BigQuery
  --   OPEN c
  -- TODO(review): FETCH is not supported; rewrite manually
  -- TODO(manual): FETCH is not supported in BigQuery
  --   FETCH LAST FROM c INTO @i
  -- TODO(review): FETCH is not supported; rewrite manually
  -- TODO(manual): FETCH is not supported in BigQuery
  --   FETCH PRIOR FROM c INTO @i
  -- TODO(review): CLOSE is not supported; rewrite manually
  -- TODO(manual): CLOSE is not supported in BigQuery
  --   CLOSE c
  -- TODO(review): DEALLOCATE is not supported; rewrite manually
  -- TODO(manual): DEALLOCATE is not supported in BigQuery
  --   DEALLOCATE c
END;

-- Source: dbo.probe_101_sp_executesql (SQL Server stored procedure)
-- Probe 101: sp_executesql with parameters
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_101_sp_executesql`()
BEGIN
  -- TODO(review): sp_executesql converted to EXECUTE IMMEDIATE ... USING; check the result
  EXECUTE IMMEDIATE r"""SELECT
  *
FROM `resource1.sql_server_centralasia_salesdb.T`
WHERE
  Id = @id""" USING 1 AS id;
END;

-- Source: dbo.probe_102_exec_concat (SQL Server stored procedure)
-- Probe 102: EXEC(@sql) with concatenated string
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_102_exec_concat`()
BEGIN
  DECLARE v_n STRING;
  DECLARE v_sql STRING;
  SET v_n = 'T';
  SET v_sql = 'SELECT * FROM dbo.' || v_n;
  -- TODO(review): dynamic SQL: the string being executed is still T-SQL and must be converted too
  -- TODO(review): SECURITY: dynamic SQL is built by string concatenation (@sql). If any part comes from a parameter or table data this is an SQL injection risk: pass values with EXECUTE IMMEDIATE ... USING, and allow-list identifiers (table / column names) instead of concatenating them
  EXECUTE IMMEDIATE v_sql;
END;

-- Source: dbo.probe_103_dyn_const_var (SQL Server stored procedure)
-- Probe 103: Constant dynamic SQL stored in a variable
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_103_dyn_const_var`()
BEGIN
  DECLARE v_sql STRING;
  SET v_sql = r"""UPDATE `resource1.sql_server_centralasia_salesdb.T` SET LastRun = CURRENT_DATETIME()
WHERE
  Id = @id""";
  -- TODO(review): sp_executesql converted to EXECUTE IMMEDIATE ... USING; check the result
  EXECUTE IMMEDIATE v_sql USING 1 AS id;
END;

-- Source: dbo.probe_104_print (SQL Server stored procedure)
-- Probe 104: PRINT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_104_print`()
BEGIN
  -- PRINT removed: 'hello'
END;

-- Source: dbo.probe_105_while_break (SQL Server stored procedure)
-- Probe 105: WHILE + BREAK / CONTINUE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_105_while_break`()
BEGIN
  DECLARE v_i INT64;
  SET v_i = 0;
  WHILE v_i < 5 DO
    SET v_i = v_i + 1;
    IF v_i = 2 THEN
      CONTINUE;
    END IF;
    IF v_i = 4 THEN
      BREAK;
    END IF;
  END WHILE;
  SELECT
    v_i AS i;
END;

-- Source: dbo.probe_106_if_exists (SQL Server stored procedure)
-- Probe 106: IF EXISTS (...) ... ELSE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_106_if_exists`()
BEGIN
  IF EXISTS(SELECT 1 FROM `resource1.sql_server_centralasia_salesdb.T` WHERE Id = 1) THEN
    SELECT
      1 AS a;
  ELSE
    SELECT
      0 AS a;
  END IF;
END;

-- Source: dbo.probe_107_case_expr (SQL Server stored procedure)
-- Probe 107: CASE expression
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_107_case_expr`()
BEGIN
  SELECT
    CASE WHEN Amount > 1 THEN 'h' ELSE 'l' END AS c
  FROM `resource1.sql_server_centralasia_salesdb.T`;
END;

-- Source: dbo.probe_108_declare_mid (SQL Server stored procedure)
-- Probe 108: DECLARE in the middle of the block
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_108_declare_mid`()
BEGIN
  DECLARE v_x INT64;
  SELECT
    1 AS a;
  SET v_x = 5;
  SELECT
    v_x AS x;
END;

-- Source: dbo.probe_109_select_assign (SQL Server stored procedure)
-- Probe 109: SELECT @var = col FROM ...
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_109_select_assign`()
BEGIN
  DECLARE v_n STRING;
  -- NOTE: SELECT @var = ... FROM ... assigns the LAST row in T-SQL; BigQuery version uses LIMIT 1 (add ORDER BY if the row matters). If NO row matches, T-SQL keeps the old value but BigQuery sets the variable to NULL
  SET v_n = (
    SELECT
      Name
    FROM `resource1.sql_server_centralasia_salesdb.T`
    WHERE
      Id = 1
    LIMIT 1
  );
  SELECT
    v_n AS n;
END;

-- Source: dbo.probe_110_exec_output (SQL Server stored procedure)
-- Probe 110: EXEC proc with OUTPUT parameter
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_110_exec_output`()
BEGIN
  DECLARE v_r INT64;
  -- TODO(review): named arguments in call to usp_Other: callee not found, check argument order
  CALL `resource1.sql_server_centralasia_salesdb.usp_Other`(1  /* in */, v_r  /* out */);
  SELECT
    v_r AS r;
END;

-- Source: dbo.probe_111_default_params (SQL Server stored procedure)
-- Probe 111: Default parameter values
-- Parameter defaults are not supported by BigQuery; callers must pass every argument:
--   @a = 5, @b = 'x'
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_111_default_params`(
  IN p_a INT64,
  IN p_b STRING
)
BEGIN
  SELECT
    p_a AS a,
    p_b AS b;
END;

-- Source: dbo.probe_112_return_code (SQL Server stored procedure)
-- Probe 112: RETURN <number>
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_112_return_code`()
BEGIN
  -- NOTE: RETURN value is discarded (BigQuery procedures return no status code; set config 'return_status_mode': 'out_param' to keep it in an OUT parameter)
  RETURN;
END;

-- Source: dbo.probe_113_multi_resultset (SQL Server stored procedure)
-- Probe 113: Several SELECT result sets
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_113_multi_resultset`()
BEGIN
  SELECT
    1 AS a;
  SELECT
    2 AS b;
END;

-- Source: dbo.probe_114_tvp (SQL Server stored procedure)
-- Probe 114: Table-valued parameter (READONLY)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_114_tvp`(
  -- TODO(manual): IN p_items ARRAY<STRUCT<...>>  -- T-SQL: @items dbo.ItemList READONLY
)
BEGIN
  SELECT
    COUNT(*) AS n
  FROM UNNEST(p_items);
END;

-- Source: dbo.probe_115_with_options (SQL Server stored procedure)
-- Probe 115: WITH EXECUTE AS OWNER, RECOMPILE
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_115_with_options`()
BEGIN
  SELECT
    1 AS a;
END;

-- Source: dbo.probe_116_sp_rename (SQL Server stored procedure)
-- Probe 116: System procedure (sp_rename)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_116_sp_rename`()
BEGIN
  -- NOTE: sp_rename (COLUMN) -> ALTER TABLE ... RENAME COLUMN (DDL: not allowed inside a transaction)
  ALTER TABLE `resource1.sql_server_centralasia_salesdb.T` RENAME COLUMN Name TO Name2;
END;

-- Source: dbo.probe_117_output_param (SQL Server stored procedure)
-- Probe 117: OUTPUT parameter
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_117_output_param`(
  INOUT p_r INT64
)
BEGIN
  SET p_r = 1;
END;

-- Source: dbo.probe_118_bit_compare (SQL Server stored procedure)
-- Probe 118: BIT column compared with 1
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_118_bit_compare`()
BEGIN
  SELECT
    Id
  FROM `resource1.sql_server_centralasia_salesdb.T`
  WHERE
    IsActive = TRUE;
END;

-- Source: dbo.probe_119_money (SQL Server stored procedure)
-- Probe 119: MONEY / SMALLMONEY
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_119_money`()
BEGIN
  DECLARE v_m NUMERIC;
  DECLARE v_s NUMERIC;
  SET v_m = 1.2345;
  SET v_s = 2;
  SELECT
    v_m + v_s AS t;
END;

-- Source: dbo.probe_120_guid_type (SQL Server stored procedure)
-- Probe 120: UNIQUEIDENTIFIER
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_120_guid_type`()
BEGIN
  DECLARE v_g STRING;
  SET v_g = GENERATE_UUID();
  SELECT
    v_g AS g;
END;

-- Source: dbo.probe_121_xml_type (SQL Server stored procedure)
-- Probe 121: XML type
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_121_xml_type`()
BEGIN
  DECLARE v_x STRING;
  -- TODO(review): variable @x: XML mapped to STRING
  SET v_x = '<a/>';
  SELECT
    v_x AS x;
END;

-- Source: dbo.probe_122_hierarchyid (SQL Server stored procedure)
-- Probe 122: HIERARCHYID
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_122_hierarchyid`()
BEGIN
  DECLARE v_h STRING;
  -- TODO(review): variable @h: user-defined or unsupported type 'HIERARCHYID' mapped to STRING
  SELECT
    v_h AS h;
END;

-- Source: dbo.probe_123_sql_variant (SQL Server stored procedure)
-- Probe 123: SQL_VARIANT
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_123_sql_variant`()
BEGIN
  DECLARE v_v STRING;
  -- TODO(review): variable @v: user-defined or unsupported type 'SQL_VARIANT' mapped to STRING
  SET v_v = 1;
  SELECT
    v_v AS v;
END;

-- Source: dbo.probe_124_rowversion (SQL Server stored procedure)
-- Probe 124: ROWVERSION / TIMESTAMP
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_124_rowversion`()
BEGIN
  -- TODO(review): column Ver: rowversion has no equivalent; mapped to BYTES
  CREATE OR REPLACE TEMP TABLE tmp_rv (
    Id INT64,
    Ver BYTES
  );
  SELECT
    *
  FROM tmp_rv;
END;

-- Source: dbo.probe_125_geography (SQL Server stored procedure)
-- Probe 125: GEOGRAPHY::Point / STAsText
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_125_geography`()
BEGIN
  DECLARE v_g GEOGRAPHY;
  SET v_g = ST_GEOGPOINT(2, 1);
  SELECT
    ST_ASTEXT(v_g) AS t;
END;

-- Source: dbo.probe_126_datetime_types (SQL Server stored procedure)
-- Probe 126: DATETIME2 / SMALLDATETIME / DATETIMEOFFSET
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_126_datetime_types`()
BEGIN
  DECLARE v_a DATETIME;
  DECLARE v_b DATETIME;
  DECLARE v_c TIMESTAMP;
  SET v_a = CURRENT_DATETIME();
  SET v_b = CURRENT_DATETIME();
  SET v_c = CURRENT_TIMESTAMP();
  SELECT
    v_a AS a,
    v_b AS b,
    v_c AS c;
END;

-- Source: dbo.probe_127_binary_types (SQL Server stored procedure)
-- Probe 127: VARBINARY(MAX) / 0x literal
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_127_binary_types`()
BEGIN
  DECLARE v_b BYTES;
  SET v_b = FROM_HEX('0102');
  SELECT
    v_b AS b;
END;

-- Source: dbo.probe_128_nvarchar_max (SQL Server stored procedure)
-- Probe 128: NVARCHAR(MAX)
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_128_nvarchar_max`()
BEGIN
  DECLARE v_s STRING;
  SET v_s = 'x';
  SELECT
    v_s AS s;
END;

-- Source: dbo.probe_129_temp_table (SQL Server stored procedure)
-- Probe 129: #temp table
CREATE OR REPLACE PROCEDURE `resource1.sql_server_centralasia_salesdb.probe_129_temp_table`()
BEGIN
  CREATE OR REPLACE TEMP TABLE tmp_t (
    Id INT64,
    Name STRING(10)
  );
  INSERT INTO tmp_t
  VALUES
    (1, 'a');
  SELECT
    *
  FROM tmp_t;
  DROP TABLE IF EXISTS tmp_t;
END;
