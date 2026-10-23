# tsql2bq – SQL Server stored procedures **and views** → BigQuery

Reads every `.sql` file in **`stored_procedures_slq_sever/`** and writes one BigQuery script per object
(`CREATE OR REPLACE PROCEDURE` / `CREATE OR REPLACE VIEW`) into **`bigquery_sql/`**, plus:

* `conversion_report.md` / `.json` – everything that needs a human look
* `deploy_order.txt` and `deploy_all.sql` – all converted objects in a safe order (views first, a view that reads
  another view comes after it, then procedures)

```
python convert.py                                   # defaults: folders above + config.json
python convert.py -i my_sql_folder -o out_folder
python convert.py --project my-proj --dataset my_dataset
python convert.py --fail-on-review                  # exit code 3 if anything needs review (CI)
python convert.py --return-status-mode out_param    # keep RETURN <n> in an OUT parameter
python -m unittest discover -s tests                # run the tests (132, incl. a golden test over 129 probes)
python tools/dry_run.py bigquery_sql --location US  # dry-run every converted object with the bq CLI
```

v5 changes are listed in `CHANGES.md`; `REVIEW.md` has the code review (bugs fixed, remaining risks, performance,
security and migration best practices).

No install needed: a tested copy of `sqlglot` is bundled in `vendor/` and is always used (a different pip-installed sqlglot version changes the AST and breaks conversions). Python 3.9+.

## Folder layout / datasets

Put objects in sub-folders named after the database and map them in `config.json`:

```
stored_procedures_slq_sever/salesdb/usp_a.sql  ->  bigquery_sql/salesdb/usp_a.sql   (dataset sql_server_centralasia_salesdb)
stored_procedures_slq_sever/hrdb/usp_b.sql     ->  bigquery_sql/hrdb/usp_b.sql      (dataset sql_server_us_hrdb)
```

Files directly in the input folder use `default_dataset`. A file may contain several objects and `GO`
separators; each object gets its own output file.

## config.json

| key | meaning |
|---|---|
| `project`, `default_dataset`, `dataset_map` | target project / datasets (folder name → dataset) |
| `param_prefix` `p_`, `var_prefix` `v_`, `temp_prefix` `tmp_` | prefixes so variables never collide with column names |
| `schema_mode` | `prefix`: `hr.Employees` → `hr_Employees` (must match how the data was loaded); `drop`: → `Employees`. `dbo` is always dropped |
| `datetime_mode` | `datetime` (default): `GETDATE()` → `CURRENT_DATETIME()`; `timestamp`: → `CURRENT_TIMESTAMP()` |
| `out_param_mode` | `INOUT` (default, same semantics as T-SQL `OUTPUT`) or `OUT` |
| `bit_columns` | table columns that are `BIT` (BOOL in BigQuery) → `col = 1` becomes `col = TRUE`, also in `SET`/`INSERT` |
| `column_types` | optional `{"OrderDate": "datetime", "IsActive": "bit"}` – lets the converter choose `DATE_ADD` vs `DATETIME_ADD`, `FORMAT_DATE` vs `FORMAT_DATETIME`, and silence the BIT warning |
| `keep_print` | `PRINT` → `SELECT` instead of a comment |
| `return_status_mode` | `drop` (default): `RETURN <n>` is discarded; `out_param`: an `OUT return_status INT64` parameter is added and `EXEC @rc = proc` passes `@rc` |
| `table_types` | user-defined table types for table-valued parameters (`{"dbo.ItemList": "Id INT64, Name STRING"}`); `CREATE TYPE ... AS TABLE` files in the input folder are read automatically |
| `case_sensitivity` | SQL Server compares text case-insensitively, BigQuery does not. `warn` (default): flag every string column compared with a text literal; `lower`: wrap both sides in `LOWER()` so `'NEW'` also matches `'new'` (faithful, but defeats clustering/pruning); `off` |

Unknown keys (typos) are reported on the console and invalid values stop the run - v4 ignored both silently.

**Recommended:** run `tools/export_schema.sql` on each SQL Server database, save the grid as `columns.csv` and run
`python tools/schema_to_config.py columns.csv`. That fills `column_types` (BIT / date / datetime columns) so the type-sensitive
conversions are exact instead of best-effort.

## What is converted

| T-SQL | BigQuery |
|---|---|
| `CREATE/ALTER/CREATE OR ALTER PROC s.n @p T = def, @o T OUTPUT` | `CREATE OR REPLACE PROCEDURE \`proj.ds.n\`(IN p_p T, INOUT p_o T)` (defaults listed in a comment) |
| `CREATE/ALTER VIEW s.v (cols) WITH … AS select [WITH CHECK OPTION]` | `CREATE OR REPLACE VIEW \`proj.ds.v\` (cols) AS …` (options dropped and noted) |
| `DECLARE @x T = v` | `DECLARE v_x T;` hoisted to the top + `SET v_x = v;` in place |
| `@var` names | prefixed (`p_`/`v_`) so they never clash with column names |
| `SET @x = e`, `SET @x += e`, `SELECT @a = c1, @b = c2 FROM …` | `SET` / `SET (a, b) = (SELECT AS STRUCT …)`; `LIMIT 1` only when the query can return several rows |
| `IF / ELSE IF / ELSE`, `WHILE`, `BREAK`, `CONTINUE` | `IF … ELSEIF … END IF`, `WHILE … DO … END WHILE`, … |
| `TRY/CATCH`, `THROW`, `RAISERROR`, `ERROR_MESSAGE()` | `BEGIN … EXCEPTION WHEN ERROR THEN …`, `RAISE`, `@@error.message` |
| `ERROR_NUMBER/LINE/SEVERITY/STATE()` | `CAST(NULL AS INT64)` (no BigQuery equivalent, noted) |
| `ERROR_PROCEDURE()` | the procedure name as a string literal (`@@error.stack_trace` is an `ARRAY<STRUCT>`, so it cannot go into a STRING column) |
| `BEGIN TRAN / COMMIT / ROLLBACK` | `BEGIN TRANSACTION / COMMIT TRANSACTION / ROLLBACK TRANSACTION` |
| `EXEC proc @a=1` | `CALL …` – named args re-ordered, defaults filled (callee in the input set). **OUTPUT arguments that are not variables (`NULL`, missing) get a generated variable** (`v_out0_<param>`) |
| **cursors**: `DECLARE c CURSOR FOR select; OPEN; FETCH…INTO; WHILE @@FETCH_STATUS=0 … FETCH…; CLOSE; DEALLOCATE` | `FOR rec_c IN (select) DO SET v_a = rec_c.col; … END FOR;` (other cursor shapes are flagged) |
| **`WITH cte … UPDATE / DELETE / MERGE`** | CTEs inlined as subqueries (BigQuery has no `WITH` before DML) |
| `UPDATE … FROM … JOIN`, `DELETE a FROM a JOIN …` | `UPDATE … FROM … WHERE`, `DELETE … WHERE EXISTS (…)`; missing `WHERE` → `WHERE TRUE` |
| `MERGE` | `MERGE INTO …` (via sqlglot) |
| `#temp`, `SELECT … INTO #t`, `DECLARE @t TABLE(…)` | `CREATE OR REPLACE TEMP TABLE tmp_t …` (re-runnable in one script), `DROP TABLE IF EXISTS` |
| **dates**: `GETDATE`, `SYSDATETIME`, `GETUTCDATE`, `SYSDATETIMEOFFSET` | `CURRENT_DATETIME()`, `CURRENT_DATETIME('UTC')`, `CURRENT_TIMESTAMP()` |
| `YEAR(x)`, `MONTH(x)`, `DAY(x)`, `DATEPART(part, x)` | `EXTRACT(YEAR FROM x)` … |
| `DATEDIFF`, `DATEADD`, `FORMAT(d,'…')`, `CONVERT(VARCHAR(n), d, style)` | `DATETIME_DIFF`, `DATE_ADD`/`DATETIME_ADD`/`TIMESTAMP_ADD`, `FORMAT_DATE`/`FORMAT_DATETIME`/`FORMAT_TIMESTAMP` chosen by the argument's type; common styles (23, 101, 103, 112, 120, 121, 126 …) mapped |
| **BIT** variables/parameters/arguments | `BOOL` with `TRUE`/`FALSE` (`SET @f = 1` → `SET v_f = TRUE`) |
| `EXEC('constant sql')`, `sp_executesql N'…', N'@p T', @p = x` | `EXECUTE IMMEDIATE r"""converted sql""" USING x AS p` (constant strings are converted; built strings stay as-is and are flagged) |
| `SET @sql = N'constant'` … `EXEC sp_executesql @sql, …` | the string assigned to the variable is converted too (`SET v_sql = r"""…"""`); if the variable is also built by concatenation it is flagged |
| **integer division** `int / int` | `DIV(a, b)` (T-SQL `5/2 = 2`, BigQuery `/` gives `2.5`). Operand types come from variables/parameters, literals, `CAST`, `COUNT` and `column_types`; decimal/float math keeps `/`; unknown types are flagged |
| `TOP (@var)` | `QUALIFY ROW_NUMBER() OVER (ORDER BY <same order>) <= v` (BigQuery `LIMIT` only accepts a constant); `TOP 10` stays `LIMIT 10` |
| `+` on strings, `TOP`, `ISNULL`, `CONVERT`, `[bracketed]`, `PIVOT` values … | handled by sqlglot (`\|\|`, `LIMIT`, `COALESCE`, …); PIVOT values become string literals |
| table names | `` `project.dataset.table` `` |
| `@@ROWCOUNT` | `@@row_count` |
| types | `INT→INT64`, `BIT→BOOL`, `NVARCHAR→STRING`, `DECIMAL→NUMERIC/BIGNUMERIC`, `DATETIME2→DATETIME`, … |
| `SET NOCOUNT/XACT_ABORT/…`, `PRINT`, index DDL, `WITH (NOLOCK)` | removed (kept as comments); `SET DATEFIRST/DATEFORMAT/LANGUAGE/ANSI_NULLS OFF/…` are removed **and flagged** because they change behaviour |
| `@@TRANCOUNT`, `XACT_STATE()` | emulated with a `v_in_transaction` BOOL variable maintained by BEGIN/COMMIT/ROLLBACK |
| `UPDATE t … FROM … JOIN` / `LEFT JOIN`, `DELETE t FROM …` | target resolved by alias; inner joins -> WHERE, LEFT JOIN -> correlated subqueries, anti-join DELETE -> NOT EXISTS |
| `LIKE '[0-9]%'`, `ESCAPE` | `REGEXP_CONTAINS` / backslash escapes |
| **v5** `CROSS / OUTER APPLY (SELECT ...) x` | `CROSS JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) AS x` / `LEFT JOIN ... ON TRUE` |
| **v5** `STRING_SPLIT(s, sep)` (FROM, IN (...), APPLY) | `UNNEST(SPLIT(s, sep)) AS value` (`enable_ordinal` -> `WITH OFFSET`) |
| **v5** `TOP n WITH TIES`, `TOP p PERCENT` | `QUALIFY RANK() ... <= n`, `QUALIFY ROW_NUMBER() ... <= CEIL(COUNT(*) OVER () * p / 100)` |
| **v5** `FOR JSON PATH / AUTO` (single table), `ROOT`, `WITHOUT_ARRAY_WRAPPER` | `TO_JSON_STRING(ARRAY(SELECT AS STRUCT ...))` |
| **v5** recursive CTE | `WITH RECURSIVE` |
| **v5** `PERCENTILE_CONT/DISC ... WITHIN GROUP ... OVER`, `GROUPING_ID`, `VAR/VARP/STDEVP` | `PERCENTILE_CONT(x, p) OVER`, `GROUPING()` sums, `VAR_SAMP/VAR_POP/STDDEV_POP` |
| **v5** `IF @@ERROR <> 0`, `SET @e = @@ERROR` after a statement | `BEGIN stmt EXCEPTION WHEN ERROR THEN ... END` |
| **v5** `OBJECT_ID('x') IS [NOT] NULL` | `[NOT] EXISTS (... INFORMATION_SCHEMA.TABLES / ROUTINES ...)` |
| **v5** `PATINDEX`, `PARSENAME`, `NCHAR`, `SPACE`, `CHOOSE`, `FORMATMESSAGE`, `DB_NAME()`, `SUSER_SNAME/SYSTEM_USER/USER_NAME` | `REGEXP_INSTR`, `ARRAY_REVERSE(SPLIT())[SAFE_OFFSET()]`, `CODE_POINTS_TO_STRING`, `REPEAT`, `CASE`, `FORMAT`, dataset name, `SESSION_USER()` |
| **v5** `ISDATE`, `ISNUMERIC`, `TRY_PARSE`, `PARSE`, `DIFFERENCE` | `SAFE_CAST` based expressions (flagged: accepted formats differ) |
| **v5** `geography::Point / STGeomFromText`, `.STAsText() .STDistance() .Lat .Long ...` | `ST_GEOGPOINT(long, lat)`, `ST_GEOGFROMTEXT`, `ST_ASTEXT`, `ST_DISTANCE`, `ST_Y`, `ST_X` ... |
| **v5** `x AT TIME ZONE 'India Standard Time'` | `TIMESTAMP(x, 'Asia/Kolkata')` (Windows zone names mapped to IANA) |
| **v5** table-valued parameters (`@t dbo.Type READONLY`), `DECLARE @t dbo.Type` | `ARRAY<STRUCT<...>>` + `UNNEST(p_t)`, TEMP TABLE; callers pass `ARRAY(SELECT AS STRUCT * FROM tmp_x)` |
| **v5** nested `BEGIN TRAN`, `XACT_STATE()` in CATCH | inner BEGIN/COMMIT removed; `XACT_STATE()` = -1 inside the handler |
| **v5** `EXEC sp_rename` | `ALTER TABLE ... RENAME COLUMN / RENAME TO` |
| **v5** `CAST(decimal AS INT)` | `CAST(TRUNC(x) AS INT64)` (T-SQL truncates, BigQuery rounds) |
| `FOR XML PATH('')` string lists | `STRING_AGG` |

## What is flagged for manual work

Anything the tool cannot translate safely is left as `-- TODO(manual): …` (with the original T-SQL), or a
`-- TODO(review): …` comment above a statement that was converted but needs checking:
cursors in other shapes, dynamic SQL built by string concatenation (also flagged as an SQL-injection risk),
`OUTPUT` clauses, APPLY over table-valued functions, `SCOPE_IDENTITY`/identity columns, sequences, linked servers,
`UPDATE/DELETE TOP`, `WAITFOR`, `GOTO`, savepoints, outer-join `UPDATE/DELETE … FROM`, columns that look like
BIT flags (`IsActive = 1`) while `bit_columns` is not configured, `CONVERT` styles that are not mapped, etc.
**Always read `conversion_report.md`.**

## Limits worth knowing

* Division of two **columns with unknown type** is only flagged, not converted - list them in `column_types` (`{"Qty": "int"}`) and it is converted to `DIV()`.
* Case-insensitivity is only handled for comparisons with a text literal (`=`, `<>`, `IN`, `LIKE`). Column-to-column comparisons, joins, `GROUP BY`, `DISTINCT` and `ORDER BY` on text are not adjusted.
* See `CHANGES.md` (v4) for the full list of edge cases that are converted, flagged, or invisible to the tool.
* The output is **syntax-converted, never executed** (and was not run against BigQuery while building this version).
  `OK` in the report only means *no known problem pattern was detected*. Dry-run every object in BigQuery and compare
  results with SQL Server on test data before relying on it.
* BigQuery has no default parameter values – callers must pass every argument.
* `RETURN <n>` status codes are dropped by default (`EXEC @rc = proc` gets `SET v_rc = 0` only when the callee never returns a value) - set `return_status_mode: out_param` to keep them; `PRINT` becomes a comment.
* `UPDATE … FROM … JOIN`: T-SQL silently uses one source row when several match a target row; BigQuery fails with
  *"UPDATE/MERGE must match at most one source row for each target row"*. The converter adds a note; make the source unique (aggregate it).
* `deploy_order.txt` puts views first and creates a called procedure before the procedure that `CALL`s it.
* `SELECT @x = col FROM t` takes the *last* row in T-SQL; the BigQuery version uses `LIMIT 1` (add an `ORDER BY` if it matters).
* T-SQL implicit conversions do not exist in BigQuery (e.g. comparing a DATE column with a DATETIME value): the converter
  only fixes the cases it can see; the rest show up as errors on the dry run.
* `schema_mode: prefix` renames tables (`hr.Employees` → `hr_Employees`): your data load must use the same names.
