# tsql2bq v4 → v5: code review and migration risks

Scope: the converter code (`tsql2bq/`), its tests, the tools, and the 129-probe comparison with the BigQuery
Migration Service (BQMS). Everything below comes from reading code and generated SQL. Nothing was executed in BigQuery.

## 1. Results on the probe pack

| | v4 (pack run) | v5 | BQMS |
|---|---|---|---|
| Converted | 41 | 74 | 42 |
| Converted - verify | 27 | 30 | 25 |
| Flagged for manual rewrite | 39 | 25 | 32 |
| Silent problem (no warning) | **22** | **0** | **30** |

BQMS is still better on 2 probes:
* **036 `CHARINDEX`**: BQMS makes it case-insensitive by default. v5 does the same only with
  `case_sensitivity: lower`, and otherwise adds a note.
* **102 concatenated dynamic SQL**: both emit `EXECUTE IMMEDIATE`. v5 deliberately flags it, because the string
  is still T-SQL and is an injection risk.

> **Note:** the uploaded v4 zip is not the build that produced `script_output/` in the probe pack. It differs on 39
> probes. Some of those are better (UPDATE/DELETE TOP, LIKE classes, DATALENGTH). Some are worse (`TRY_CONVERT` →
> `CAST`, `XACT_STATE`, `CURRENT_USER`). Keep the probe outputs under version control with the code. v5 adds
> `tests/test_golden.py` for exactly this.

## 2. Bugs found in v4 (all fixed in v5, each with a test)

| Severity | Bug | Effect |
|---|---|---|
| High | `XACT_STATE()` emulation returned 1/0, so `IF XACT_STATE() = -1 ROLLBACK` in CATCH could never fire | The transaction is left open after an error |
| High | Table-valued parameter `FROM @items` became `` FROM `project.ds.p_items` `` | Reads a permanent table that does not exist, or the wrong one |
| High | `TRY_CONVERT` became `CAST` (the v4 CONVERT → CAST fix caught it too) | Bad data raises an error instead of returning NULL |
| High | A multi-line sqlglot error was written into a `-- TODO` comment, and its 2nd line ended up outside the comment | Invalid SQL emitted as live code, with ANSI colour codes |
| Medium | Nested `BEGIN TRAN` passed through unchanged | BigQuery rejects nested transactions |
| Medium | `SUSER_SNAME` / `SYSTEM_USER` → `CURRENT_USER()` | Not a BigQuery function |
| Medium | `PARSENAME` → `SPLIT_PART` | Not a BigQuery function |
| Medium | `datetime AT TIME ZONE` → `DATETIME(x, 'UTC')` | Invalid for a DATETIME argument |
| Medium | UNPIVOT IN-list quoted as strings | Invalid UNPIVOT |
| Medium | `CAST(2.7 AS INT)` | 3 in BigQuery, 2 in T-SQL (silent numeric difference) |
| Medium | Watchlist never checked IF/WHILE conditions | `IF OBJECT_ID(...)`, `WHILE HOST_NAME() ...` were silent |
| Low | Config typos and invalid values ignored | `"bit_column": [...]` silently did nothing |
| Low | Non-UTF-8 files decoded with replacement characters, silently | Corrupted string literals |
| Low | `export_schema.sql` exported no integer/decimal columns | `col / col` was never converted to `DIV()` automatically |

## 3. Risks that remain (the tool cannot see them)

**Semantics**
* **Case and trailing spaces.** SQL Server's default collation is case- and trailing-space-insensitive, BigQuery
  is neither. Only comparisons with a literal are flagged or fixed. Joins, `GROUP BY`, `DISTINCT` and `IN (subquery)`
  on text can return different rows. Decide per column: normalise the data at load time (`UPPER`/`TRIM`) or use
  `COLLATE(col, 'und:ci')` on the BigQuery column definition.
* **Integer arithmetic.** T-SQL `INT` is 32-bit and overflows with an error, while BigQuery INT64 does not. `AVG(int)`,
  `int / int` and `CAST(decimal AS INT)` are handled when types are known. Unknown column types are still only
  flagged, so run `export_schema.sql`.
* **Implicit conversions.** `varchar_col = 123` and `date_col = '01/02/2020'` work in T-SQL but fail or differ in
  BigQuery. The dry run catches type errors. Culture-specific date strings need `PARSE_DATE` with a format.
* **`UPDATE ... FROM` with several matching source rows.** T-SQL picks one row, BigQuery raises an error. This only
  appears at runtime, on real data.
* **`SELECT @v = col FROM t`.** T-SQL assigns the last row, BigQuery uses `LIMIT 1`. If no row matches, T-SQL keeps
  the old value and BigQuery sets NULL.
* **Error flow.** `RAISERROR` outside TRY continues in T-SQL, while BigQuery `RAISE` stops. Statement-level errors
  without TRY continue in T-SQL but abort the script in BigQuery. The `@@ERROR` rewrite covers the explicit-check
  pattern only.
* **Transactions.** `@@TRANCOUNT` emulation only sees this procedure's own BEGIN/COMMIT, not a transaction opened by
  the caller. BigQuery transactions use snapshot isolation, and concurrent DML on the same table can abort one
  transaction. Expect retries where T-SQL used locks (`WITH (UPDLOCK)`, `HOLDLOCK`).
* **Result sets.** A procedure with several `SELECT`s: client libraries usually read only the last result. Check what
  the calling application expects.
* **Time.** `GETDATE()` is server-local time in SQL Server, while `CURRENT_DATETIME()` is UTC unless you pass a zone.
  For Azure SQL (UTC) this is usually the same; for on-premises servers it is not. Consider
  `datetime_mode` or `CURRENT_DATETIME('Asia/Kolkata')`.
* **JSON.** `FOR JSON` omits NULL properties by default, while `TO_JSON_STRING` writes `"col":null`. Number and date
  formatting can also differ slightly. Compare one sample output.

**Behaviour you must re-create outside the procedure**
* `EXECUTE AS OWNER` is dropped. BigQuery procedures run with the caller's permissions, so every caller needs access
  to the underlying tables (or use authorized views / routines where they fit).
* `GRANT` / `DENY` become TODOs. Permissions are IAM roles on the project, dataset, table or routine.
* Triggers, identity columns, sequences, defaults and CHECK/FK constraints do not exist (or are not enforced).
  Move that logic into the loading procedures and validate it with the reconciliation tool.
* Global `##temp` tables and temp tables shared between sessions have no BigQuery equivalent. Use a real table with a
  session key, or restructure.

## 4. Performance

* **Row-by-row code.** A cursor becomes a `FOR` loop with one DML per row. In BigQuery every DML statement is a job
  that scans the table, and mutating DML on one table is serialised. Rewrite loops as one set-based
  `INSERT ... SELECT` / `MERGE` (README section *Needs your decision*, item 5).
* **`EXECUTE IMMEDIATE` inside loops** creates one job per iteration. Build one statement, or use `UNNEST` over an array
  of values.
* **Partition pruning.** `EXTRACT(YEAR FROM d) = 2024`, `LOWER(col) = ...` (`case_sensitivity: lower`) and functions on
  the partition column prevent pruning. Use range predicates on the raw column.
* **`SELECT *`** is billed per column read. Many converted queries keep `SELECT *` from T-SQL, so list the columns in
  hot paths.
* **Correlated APPLY conversions** (`UNNEST(ARRAY(SELECT AS STRUCT ...))`) run once per outer row unless BigQuery
  de-correlates them. For large tables, rewrite as a join with a pre-aggregated subquery.
* **Temp tables** are billed as storage for the session. `CREATE OR REPLACE TEMP TABLE` in a loop is a full write each
  time.

## 5. Security

* **Dynamic SQL built by concatenation** is now flagged as `SECURITY`. Pass values with `EXECUTE IMMEDIATE ... USING`,
  and check identifiers against an allow-list (or `INFORMATION_SCHEMA`) before quoting them with backticks.
  `QUOTENAME` has no direct equivalent.
* **Deployment.** Review `deploy_all.sql` before running it, and deploy with a dedicated service account that has
  `bigquery.routines.create` on the target datasets only.
* **Least privilege for callers.** Because procedures run with caller rights, avoid granting broad `dataEditor` just
  to make procedures work. Grant per dataset, or expose data through authorized views.
* **Linked servers / OPENQUERY** often embed credentials in SQL Server. Remove them; use federated / external tables
  with a connection resource.
* **The tool itself** runs offline and never connects to SQL Server or GCP. `tools/dry_run.py` calls `bq` without a
  shell and passes the SQL on stdin.

## 6. Code-level observations and recommendations

1. **`translator.py` is ~3,000 lines.** Splitting it by concern (types, DML, control flow, functions) would make it
   easier to test. v5 starts this with `fixes_v5.py` for expression rewrites.
2. **Broad `except Exception`** turns converter bugs into "could not be converted" TODOs. That is safe for output but
   hides defects. Add a `--debug` flag that re-raises, and run the golden test with it in CI.
3. **`column_types` is keyed by column name only.** `Status` as INT in one table and VARCHAR in another is ambiguous
   and gets skipped. Supporting `"Table.Column"` keys would remove the ambiguity.
4. **Vendored sqlglot has no recorded version** (`_version.py` says "vendored"). Record the commit/version so the
   AST assumptions can be re-checked when you upgrade.
5. **The `OUTPUT` clause is still emitted as `RETURNING`.** This is deliberate (it fails the dry run loudly instead of
   silently losing the captured rows), but a TODO block with the original would read more clearly.
6. **`deploy_order` searches procedure text for callee names** (O(n²)). That is fine for hundreds of objects. For
   thousands, build the call graph from the parsed `EXEC` statements instead.
7. **Tests.** 132 tests now, including a golden test over all 129 probes. Add your real procedures to
   `tests/golden` once their output has been reviewed, so later changes cannot alter them silently.

## 7. Recommended migration workflow

1. Export column types: `tools/export_schema.sql` → `tools/schema_to_config.py`. Also set `bit_columns`,
   `dataset_map` and `case_sensitivity` deliberately.
2. Put `CREATE TYPE ... AS TABLE` scripts in the input folder (TVPs), and decide `return_status_mode`.
3. Convert with `--fail-on-review` in CI. Fix or accept each REVIEW item in `conversion_report.md`.
4. Dry-run everything: `python tools/dry_run.py bigquery_sql --location <region>`.
5. Run each procedure on test data in both systems and compare results (row counts, sums, hashes per column) with
   your reconciliation tool before switching callers over.
6. Re-run the BQMS comparison for objects where the two tools disagree. It is a good second opinion on
   type-dependent conversions once BQMS is given schema metadata (`dwh-migration-dumper`) and name mapping
   (it kept `dbo.T` lowercase and unqualified in this run).
