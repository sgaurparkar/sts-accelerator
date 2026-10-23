# Changes in v5 (gap review against BigQuery Migration Service)

The 129-probe pack was run through the BigQuery Migration Service (BQMS) batch translator and through this tool, and
every output was read side by side (`probe_results_v5_comparison.xlsx`). v5 closes the gaps where BQMS did better or
where both produced wrong output. `python -m unittest discover -s tests` now runs **132 tests** (93 from v4, 39 new in
`tests/test_v5.py` and `tests/test_golden.py`, which locks the output of all 129 probes), all passing.

| | v4 (pack run) | **v5** | BQMS |
|---|---|---|---|
| Converted | 41 | **74** | 42 |
| Converted - verify | 27 | **30** | 25 |
| Flagged for manual rewrite | 39 | **25** | 32 |
| **Silent problem** (wrong / left as T-SQL, no warning) | 22 | **0** | 30 |

Head-to-head, BQMS is better on 2 probes (was 28), v5 on 59, the same on 68. Nothing was executed in BigQuery - the
counts come from reading the outputs; run `tools/dry_run.py` on your own objects.

## Bugs fixed (v4 produced wrong or invalid output)

| Input | v4 | v5 |
|---|---|---|
| `TRY_CONVERT(INT, x)` | `CAST(x AS INT64)` - errors on bad data (the CONVERT->CAST fix also caught TRY_CONVERT) | `SAFE_CAST`; `TRY_CONVERT(DATE, x, 103)` -> `SAFE.PARSE_DATE` |
| `XACT_STATE() = -1` in CATCH | `CASE ... THEN 1 ELSE 0 END = -1` - **never true, the ROLLBACK never ran** | -1 inside the exception handler |
| nested `BEGIN TRAN` | passed through - BigQuery fails on a nested transaction | inner BEGIN/COMMIT removed (T-SQL only changes @@TRANCOUNT); IF/ELSE branches tracked separately |
| `SUSER_SNAME()`, `SYSTEM_USER`, `USER_NAME()` | `CURRENT_USER()` (not a BigQuery function) | `SESSION_USER()` |
| `PARSENAME('a.b.c', 1)` | `SPLIT_PART(...)` (not a BigQuery function) | constant folded / `ARRAY_REVERSE(SPLIT(s, '.'))[SAFE_OFFSET(n-1)]` |
| `datetime AT TIME ZONE 'UTC'` | `TIMESTAMP(DATETIME(x, 'UTC'))` - invalid for a DATETIME | `TIMESTAMP(x, 'UTC')`; Windows zone names mapped to IANA (`India Standard Time` -> `Asia/Kolkata`) |
| `UNPIVOT (v FOR c IN (A, B))` | `IN ('A' AS A, ...)` - columns quoted as strings | columns kept |
| table-valued parameter | body read a **permanent table `p_items`** in the dataset, silently | `ARRAY<STRUCT<...>>` + `UNNEST(p_items)`; unknown type flagged |
| `RAND(42)` | passed through (RAND takes no seed) | `RAND()` + review |
| `CAST(2.7 AS INT)` | `CAST(2.7 AS INT64)` = 3 (BigQuery rounds, T-SQL truncates to 2) | `CAST(TRUNC(2.7) AS INT64)` |
| a sqlglot error with a line break | the 2nd line of the message was written **outside** the `--` comment, i.e. as live (invalid) SQL, with terminal colour codes | messages are one line, colour codes stripped |
| functions in `IF` / `WHILE` conditions | never checked against the watchlist (e.g. `IF OBJECT_ID(...)` passed silently) | checked |
| `COLLATE ..._CS_AS` | dropped, then a false "case-insensitive" warning | no warning (BigQuery is case-sensitive already) |
| `config.json` typos (`bit_column`) / bad values | silently ignored | reported / run stops with the allowed values |
| non-UTF-8 input file | decoded as cp1252 with replacement characters, silently | REVIEW note on every object of the file; UTF-16 without BOM detected |

## Newly converted (v4 flagged or left them)
CROSS/OUTER APPLY -> `[CROSS|LEFT] JOIN UNNEST(ARRAY(SELECT AS STRUCT ...))`; `STRING_SPLIT` (FROM, `IN (...)`, APPLY,
`enable_ordinal`); `TOP n WITH TIES` / `TOP p PERCENT` -> QUALIFY; `FOR JSON PATH/AUTO` -> `TO_JSON_STRING(ARRAY(...))`;
recursive CTE -> `WITH RECURSIVE`; `PERCENTILE_CONT/DISC WITHIN GROUP OVER`; `GROUPING_ID`; `VAR/VARP/STDEVP`;
`IF @@ERROR <> 0` / `SET @e = @@ERROR` -> exception handler; `ERROR_LINE()` -> stack trace line; `OBJECT_ID() IS [NOT]
NULL` -> INFORMATION_SCHEMA; `DB_NAME()`; `PATINDEX`; `NCHAR`; `SPACE`; `CHOOSE`; `FORMATMESSAGE`; `ISDATE`/`ISNUMERIC`/
`TRY_PARSE`/`PARSE` (flagged: accepted formats differ); `DIFFERENCE` (approximation, flagged); geography/geometry
constructors and methods -> `ST_*`; `sp_rename` -> `ALTER TABLE ... RENAME`; `CREATE TYPE ... AS TABLE` read from the
input folder (TVPs and `DECLARE @t dbo.Type`); `INSERT ... EXEC` gets a specific TODO.

## New options / tools
* `return_status_mode: out_param` (`--return-status-mode`): `RETURN <n>` kept in `OUT p_return_status INT64`;
  `EXEC @rc = proc` passes `@rc`.
* `table_types` in config.json (filled automatically from CREATE TYPE files).
* `--fail-on-review`: exit code 3 when something needs review (CI).
* `tools/dry_run.py`: dry-runs every object in deploy order with `bq`, writes `dry_run_results.csv`.
* `tools/export_schema.sql` now exports integer / decimal / float columns too (v4 only BIT and dates, so
  `col / col` integer division was never converted automatically).

## New warnings
SECURITY review on dynamic SQL built by concatenation (SQL injection); RAND() evaluated per row in BigQuery but once per
query in T-SQL; CHARINDEX case-sensitivity; full-text CONTAINS -> CONTAINS_SUBSTR semantics (operators flagged);
FOR JSON NULL-field handling; recursive CTE iteration limits; GEOMETRY -> GEOGRAPHY; SRID other than 4326.

## Still flagged for manual work
`OUTPUT` clauses (incl. MERGE `$action`), `UPDATE/DELETE TOP (n)`, `INSERT ... EXEC`, sequences / identity /
`SCOPE_IDENTITY`, savepoints, GOTO, WAITFOR, cursors other than the FETCH/WHILE pattern, linked servers / OPENQUERY,
OPENJSON / OPENXML / XML methods, dynamic SQL built by concatenation, SESSION_CONTEXT, `@@SPID`/`@@VERSION`/`HOST_NAME`,
`HASHBYTES` (UTF-16 vs UTF-8 bytes) / `CHECKSUM`, SQL_VARIANT. See `REVIEW.md` for how to approach each.

# Changes in v4 (edge-case review)

The v3 converter was run against ~120 extra T-SQL probe cases on top of the 9 sample objects. Many produced
**invalid BigQuery or, worse, valid SQL that does something different, with no warning**. v4 fixes those and adds
46 regression tests (`tests/test_v4_edge_cases.py`); `python -m unittest discover -s tests` runs 93 tests, all passing.
The 9 sample objects convert the same as in v3 except for the `@@TRANCOUNT` fix below.

## Critical: wrong results or wrong table, silently (fixed)

| Input | v3 output | v4 |
|---|---|---|
| `UPDATE t SET ... FROM dbo.S s JOIN dbo.T t ON ...` (target alias not first in FROM) | `UPDATE `p.ds.t` ... WHERE TRUE` - a table literally named `t`, no join condition, **no warning** | target resolved by alias wherever it is in the FROM list, ON conditions moved to WHERE |
| `UPDATE t SET ... FROM dbo.T t LEFT JOIN dbo.S s ...` | `UPDATE `p.ds.t`` + still a LEFT JOIN (invalid) | correlated subqueries `SET v = (SELECT s.v FROM S s WHERE ...)` (same semantics: unmatched rows get NULL) |
| `DELETE s FROM dbo.T t JOIN dbo.S s ...` | deleted from **T** (the first table) | deletes from the table named after DELETE (`S`) with `WHERE EXISTS` |
| `DELETE t FROM dbo.T t WHERE ...` | `DELETE `p.ds.t` FROM ...` (invalid) | `DELETE FROM `p.ds.T` AS t WHERE ...` |
| `DELETE t FROM T t LEFT JOIN S s ON ... WHERE s.id IS NULL` | flagged only | `WHERE NOT EXISTS (...)` |
| `DELETE TOP (n)` / `UPDATE TOP (n)` | `DELETE `p.ds.TOP` FROM ...` garbage / parse error | TODO block with the rewrite to use |
| `DROP TABLE IF EXISTS #x` (SQL Server 2016+) | parser split it into `DROP TABLE` + `IF EXISTS ...` and wrapped the **next** statement in `IF TRUE THEN ... END IF` | one statement `DROP TABLE IF EXISTS tmp_x` |
| `/* a /* nested */ b */` comment | text after the inner `*/` emitted **as code** (`still AS comment;`) | T-SQL nested comments supported |
| `'...` + newline + `GO` + newline + `...'` inside a string / block comment | batch split in the middle of the string | GO only counts outside strings and comments |
| `DECLARE @x INT` (no initialiser) inside a loop | `SET v_x = NULL` every iteration - T-SQL does **not** reset it, so running totals came out wrong (3 became 2 in the probe) | nothing emitted (T-SQL semantics) |
| `IF @@TRANCOUNT > 0 ROLLBACK` in CATCH | guard removed: unconditional `ROLLBACK` fails (and hides the real error) when the error happened before `BEGIN TRAN` | emulated with `v_in_transaction` (`DECLARE ... BOOL DEFAULT FALSE`, set by BEGIN/COMMIT/ROLLBACK); `XACT_STATE()` mapped to it too |
| `ROLLBACK TRAN savepoint` | `ROLLBACK TRANSACTION` - rolls back the **whole** transaction | TODO + review |
| `case_sensitivity: "lower"` | `UPDATE ... SET LOWER(Status) = LOWER('Done')` (invalid, and would have lower-cased data) | SET assignments are left alone (also no more false "compared with a text literal" warnings for `SET col = 'x'`) |
| `FOR XML PATH('')` / `FOR JSON` | clause **silently dropped** - query returns rows instead of one string | `STUFF((SELECT ', ' + x ... FOR XML PATH('')), 1, 2, '')` and `(SELECT ',' + x ... FOR XML PATH(''))` -> `STRING_AGG`; any other FOR XML/JSON flagged REVIEW |
| `LIKE '[0-9]%'`, `LIKE '[^a-z]%'` | kept as is: BigQuery LIKE has no character classes, so it matches literally `[0-9]` | `REGEXP_CONTAINS(col, '(?s)^[0-9].*$')`; `[_]`, `[%]` escapes -> `\_`, `\%` |
| `LIKE ... ESCAPE '!'` | kept (syntax error in BigQuery) | rewritten with backslash escapes |
| `LIKE 'C:\temp%'` | backslash is an escape in BigQuery LIKE -> matched `C:temp...` | backslashes doubled |
| `ROUND(x, 2, 1)` (truncate) | `ROUND(x, 2, 1)` - 3rd arg is a rounding mode string in BigQuery | `TRUNC(x, 2)` |
| `CONVERT(INT, x)` / `CONVERT(DATE, x)` | `SAFE_CAST` - bad data became NULL instead of an error | `CAST` (TRY_CONVERT/TRY_CAST still -> SAFE_CAST) |
| `CAST(s AS VARCHAR(5))` / `CONVERT(VARCHAR(5), s)` | `CAST(s AS STRING)` (no truncation) / `SAFE_CAST(s AS STRING(5))` (NULL if longer) | `SUBSTRING(CAST(s AS STRING), 1, 5)` like T-SQL (not applied to numbers/dates) |
| `CONVERT(DATETIME, '2020-01-01', 120)` | `PARSE_TIMESTAMP('%F %T', ...)` - wrong type and fails without a time part | `CAST(... AS DATETIME)`; other styles -> `PARSE_DATETIME/PARSE_DATE(fmt, x)` |
| `AVG(int)` | FLOAT64 (T-SQL returns a truncated INT) | `CAST(TRUNC(AVG(x)) AS INT64)` when the argument is known to be INT |
| `@varchar + 1` | `v || 1` (string concat; T-SQL does numeric addition) | `CAST(v AS INT64) + 1` + review |
| `SUM(int) / COUNT(*)` | "unknown types" warning | `DIV(...)` (SUM/MIN/MAX/ABS of int are int) |
| `EXEC dbo.usp_Log ...` when `usp_Log` exists in two databases | registry ignored the database: the call got the **other** database's parameter list (missing defaults -> CALL fails), status OK | lookup by database folder first; ambiguous names never cross databases |
| two objects mapping to the same BigQuery name (`sales.usp_X` + `dbo.sales_usp_X`, or `schema_mode: drop`) | second file silently overwrote the first | FAILED "name collision", not written |
| `pip install -r requirements.txt` (sqlglot 25) | convert.py used it instead of vendor/: **37 of 47 tests fail** with sqlglot 25 | vendor/ is always used; requirements.txt says so |

## Invalid BigQuery that is now converted
`$12.50` money literals; `GROUP BY ... WITH ROLLUP/CUBE` -> `GROUP BY ROLLUP(...)`; `OFFSET @x ROWS FETCH NEXT @n ROWS`
(LIMIT/OFFSET need constants) -> `QUALIFY ROW_NUMBER() ...`; `TOP 100 PERCENT` (view idiom) removed;
`DATEADD(day, DATEDIFF(day, 0, x), 0)` (and month/year/quarter/hour) -> `DATETIME_TRUNC`; `DATETRUNC` on DATETIME/DATE ->
`DATETIME_TRUNC`/`DATE_TRUNC`; `datetime + n` -> `DATETIME_ADD(..., INTERVAL n DAY)`; `CONCAT(s, 123)` (BigQuery needs
STRING args) -> `CAST(... AS STRING)`; `ISNULL(int, '')` -> `COALESCE(x, 0)`; `LEN(x)` -> `LENGTH(RTRIM(x, ' '))`
(LEN ignores trailing spaces); `DATALENGTH` -> `BYTE_LENGTH`; `STUFF(s, a, n, r)` -> SUBSTR concatenation;
`ROWCOUNT_BIG()` -> `@@row_count`; `COLLATE ..._CS_..` dropped, `..._CI_..` -> `COLLATE(x, 'und:ci')`;
`@flag = 'true'` -> `= TRUE`; BIT columns now also in `INSERT ... SELECT 1`, `SET flag = CASE ... THEN 1 ELSE 0 END`,
`flag IN (1)`; `WHILE (@@FETCH_STATUS = 0)` / `<> -1` cursors and `UNION` cursor queries -> FOR loop; a `FETCH` right before
`CONTINUE` inside the loop is dropped (the FOR loop advances itself); `EXEC @rc = proc` -> `SET v_rc = 0` when the callee
never RETURNs a value; dynamic SQL constants: `#temp` inside the string renamed, `EXEC('a; b')` split into one
`EXECUTE IMMEDIATE` per statement; scalar UDF `dbo.fn(x)` / TVF `dbo.tvf(x)` -> `` `project.dataset.fn` `` (+ review);
procedure names with spaces -> `Get_Orders` (callers too); `RAISERROR(50001, 16, 1)` -> message string.

## New warnings (previously silent)
* REVIEW: T-SQL functions that do not exist in BigQuery (PATINDEX, STR, NCHAR, STRING_SPLIT, ISJSON, SWITCHOFFSET,
  DATETIMEFROMPARTS, TRY_PARSE, FORMAT(number, 'N2') -> NUMBER_TO_STR, ...), each with the replacement to use.
* REVIEW: `@@ROWCOUNT` read after a non-DML statement (e.g. `SELECT @v = col ...; IF @@ROWCOUNT = 0` never fires in BigQuery).
* REVIEW: `SELECT @s = @s + col FROM t` accumulation (only one row is used after conversion -> use STRING_AGG/SUM).
* NOTE: `SELECT @v = ... FROM` with no matching row keeps the old value in T-SQL but sets NULL in BigQuery.
* REVIEW: removed `SET DATEFIRST / DATEFORMAT / LANGUAGE / ANSI_NULLS OFF / CONCAT_NULL_YIELDS_NULL OFF / ANSI_WARNINGS OFF`.
* REVIEW: databases missing from `dataset_map` (were silently pointed at the default dataset) and four-part linked-server names.
* REVIEW: permanent CREATE/DROP/SELECT INTO inside a transaction (BigQuery only allows DDL on TEMP tables there).
* REVIEW: `NEXT VALUE FOR`, `EXEC @rc = proc` for callees that RETURN values, `UPDATE ... SET @var = col`, labels,
  `INSERT ... DEFAULT VALUES`, multi-statement / scripting dynamic SQL, Windows names in `AT TIME ZONE`, `CAST(datetime AS VARCHAR)`
  format, `ISNULL(date, '')`, `date - date`.
* NOTE: `DATEPART(week)` numbering differs; `RAISERROR` outside TRY continues in T-SQL but stops in BigQuery; global `##temp`.
* `CREATE FUNCTION / TRIGGER / TYPE / SEQUENCE / SYNONYM` batches are listed as SKIPPED with the reason (were silently ignored
  when the file also held a procedure). The console summary now prints the skipped count.

## Still not converted (flagged for manual work)
CROSS/OUTER APPLY (sqlglot writes `LATERAL`, which BigQuery lacks); `OUTPUT` clauses incl. MERGE `$action`; `INSERT ... EXEC`;
identity / `SCOPE_IDENTITY` / sequences; table-valued parameters; user-defined functions and triggers (only reported);
cursors other than the `FETCH ... WHILE @@FETCH_STATUS` pattern (cursor variables, SCROLL, `WHERE CURRENT OF`);
GOTO / labels / WAITFOR; `TOP ... PERCENT/WITH TIES`; `UPDATE/DELETE TOP`; UPDATE with RIGHT/FULL or several outer joins;
dynamic SQL built by concatenation and `sp_executesql` OUTPUT params; FOR XML/JSON other than the `PATH('')` idiom; OPENJSON,
XML `.value()/.nodes()`; PIVOT/UNPIVOT (verify); savepoints; `RETURN <n>` status codes.

## Edges the tool cannot see (check on the dry run / with test data)
* Case-insensitive comparisons are only handled against text literals - not joins, GROUP BY, DISTINCT, ORDER BY, IN (subquery).
* Trailing spaces: T-SQL `'a' = 'a  '` is TRUE (and `LEN` ignores them); BigQuery compares exactly. Not flagged.
* Implicit conversions on columns of unknown type (`varchar_col = 123`, `int_col = '42'`, `@d = '01/02/2020'`) - fill
  `column_types` (see `tools/schema_to_config.py`) so more of them are fixed; the rest fail on the dry run.
* `@@TRANCOUNT` emulation only sees this procedure's own BEGIN/COMMIT - a transaction opened by the caller or a callee is not tracked.
* Temp tables created by a caller and used by a called procedure (T-SQL nested scope) - verify in BigQuery.
* `UPDATE ... FROM` / LEFT-JOIN subqueries fail when the source has several rows per target row (T-SQL silently picks one).
* `CHAR(n)` padding, MONEY rounding (4 decimals), DATETIME 3.33 ms precision vs microseconds, `FLOAT` -> FLOAT64 display.
* Nothing has been executed in BigQuery. Dry-run every object (`bq query --dry_run --use_legacy_sql=false < file.sql`).

# Changes in v3

Checked against the sample run (9 SQL Server objects → expected BigQuery scripts).
Run `python -m unittest discover -s tests` – 47 tests, all passing.

## Converter bugs fixed (output was invalid BigQuery before)

| Problem | Before | Now |
|---|---|---|
| `ERROR_NUMBER()` / `ERROR_LINE()` / `ERROR_SEVERITY()` / `ERROR_STATE()` were left in the SQL | `ERROR_NUMBER() AS ErrorNumber` (does not exist in BigQuery) | `CAST(NULL AS INT64)` + note |
| `ERROR_PROCEDURE()` | left as is | the procedure name as a string. (`@@error.stack_trace` is an `ARRAY<STRUCT<line, column, filename, location>>`, so putting it in a STRING column like `ErrorProcedure` fails.) |
| `YEAR()` / `MONTH()` / `DAY()` | `YEAR(CAST(x AS DATE))` (no such function in BigQuery) | `EXTRACT(YEAR FROM x)` |
| Dynamic SQL stored in a variable (`SET @Sql = N'…'` then `EXEC sp_executesql @Sql`) | string kept as T-SQL (`sales.RegionStatistics`, `GETDATE()`, literal `\n`) | string converted: `` r"""UPDATE `proj.ds.sales_RegionStatistics` SET … CURRENT_DATETIME() …""" `` |
| `TOP (@TopN)` | `LIMIT p_TopN`. BigQuery `LIMIT` only accepts a constant, so this fails | `QUALIFY ROW_NUMBER() OVER (ORDER BY …) <= p_TopN` |
| Temp table created inside `BEGIN TRANSACTION` | `CREATE OR REPLACE TEMP TABLE` (BigQuery only lists `CREATE TEMP TABLE` as allowed in a transaction) | `CREATE TEMP TABLE` + note |
| `deploy_order.txt` | `usp_ArchiveOldOrders` was created before `usp_GetCustomerOrders`, which it CALLs | called procedures are created first |

## New warnings
* `UPDATE … FROM … JOIN`: note that BigQuery fails when a target row matches more than one source row (T-SQL silently uses one).
* BIT hint now also catches flag names like `Reviewed`, `Processed`, `Approved` (e.g. `SET h.Reviewed = 1`).
* The truncated cursor comment now ends with `...`.

## Needs your decision (cannot be fixed by the tool)
1. **BIT columns (done)**: `config.json` now has `"bit_columns": ["IsActive", "Reviewed"]`, so `IsActive = 1` → `IsActive = TRUE`
   and `SET Reviewed = 1` → `SET Reviewed = TRUE` (the columns are loaded as BOOL in BigQuery). Add any other BIT column to that
   list and re-run; remove `Reviewed` if it is really an INT column.
2. **Dataset for `sales.usp_ProcessMonthlySales`**: it lives in `hrdb/`, so `sales_*` and `audit_ErrorLog` tables point at
   `sql_server_us_hrdb`. If they are really in the sales database, move the file to `stored_procedures_slq_sever/salesdb/`.
3. **`usp_ProcessReturns`**: a staged return has `Status = 'NEW'`, so the `DELETE … WHERE r.Status = 'CANCELLED'` can never
   delete anything. Also, several returns for the same order: T-SQL refunds only one of them, BigQuery errors.
   Probably aggregate: `FROM (SELECT OrderId, SUM(Amount) AS Amount FROM tmp_ReturnStage GROUP BY OrderId) s`.
4. **`usp_ArchiveOldOrders`**: `@DryRun = 1` still deletes `OrderLines` and still merges into `OrderArchive`; only the final
   `DELETE Orders` is skipped. `@Archived` counts deleted *order lines*, not orders.
5. **`usp_ProcessCustomerCursor`**: the FOR loop runs one INSERT per customer (slow and DML-quota heavy in BigQuery).
   A single `INSERT … SELECT CustomerId, p_Prefix || FullName, CURRENT_DATETIME() FROM … WHERE IsActive` does the same.
6. **`usp_ProcessMonthlySales`**: `EXTRACT(YEAR/MONTH FROM SaleDate) = …` prevents partition pruning. If the table is
   partitioned on `SaleDate`, use `SaleDate >= DATE_TRUNC(p_ProcessingMonth, MONTH) AND SaleDate < DATE_ADD(DATE_TRUNC(p_ProcessingMonth, MONTH), INTERVAL 1 MONTH)`.
7. Nothing here has been executed. Dry-run each script in BigQuery (`bq query --dry_run`) before deploying.
