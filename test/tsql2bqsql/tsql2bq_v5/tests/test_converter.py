import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from tsql2bq.parser import parse_batch, split_batches  # noqa: E402
from tsql2bq.translator import Config, Translator, map_type  # noqa: E402
from tsql2bq.views import parse_view_batch  # noqa: E402


def convert(src, **cfg):
    config = Config(project="proj", default_dataset="ds", **cfg)
    procs = [p for p in (parse_batch(b) for b in split_batches(src)) if p]
    reg = {f"{(p.schema or 'dbo').lower()}.{p.name.lower()}": p for p in procs}
    out = [Translator(config).convert(p, "ds", reg) for p in procs]
    return out[-1][0], out[-1][1]


class TypeTests(unittest.TestCase):
    def test_types(self):
        self.assertEqual(map_type("NVARCHAR(MAX)")[0], "STRING")
        self.assertEqual(map_type("decimal(18,2)")[0], "NUMERIC")
        self.assertEqual(map_type("DECIMAL(38,10)")[0], "BIGNUMERIC")
        self.assertEqual(map_type("bit")[0], "BOOL")
        self.assertEqual(map_type("datetime2(3)")[0], "DATETIME")
        self.assertEqual(map_type("uniqueidentifier")[0], "STRING")


class ProcedureTests(unittest.TestCase):
    def test_header_params_and_prefixes(self):
        sql, _ = convert("CREATE PROC dbo.P @a INT, @b VARCHAR(5) = 'x', @c INT OUTPUT AS SELECT @a")
        self.assertIn("CREATE OR REPLACE PROCEDURE `proj.ds.P`(", sql)
        self.assertIn("IN p_a INT64,", sql)
        self.assertIn("IN p_b STRING,", sql)
        self.assertIn("INOUT p_c INT64", sql)
        self.assertIn("@b = 'x'", sql)             # default documented
        self.assertIn("SELECT\n    p_a;", sql.replace("  SELECT\n  p_a", "SELECT\n    p_a"))

    def test_declare_is_hoisted_and_initialised(self):
        sql, _ = convert("CREATE PROC P AS BEGIN SELECT 1; DECLARE @x INT = 5; SET @x += 1 END")
        lines = [l.strip() for l in sql.splitlines()]
        self.assertLess(lines.index("DECLARE v_x INT64;"), lines.index("SET v_x = 5;"))
        self.assertIn("SET v_x = v_x + 1;", lines)

    def test_if_elseif_else_while_break(self):
        sql, _ = convert("""CREATE PROC P @n INT AS
        WHILE @n < 3 BEGIN
          IF @n = 1 BREAK
          ELSE IF @n = 2 SET @n = 9
          ELSE SET @n = @n + 1
        END""")
        for frag in ("WHILE p_n < 3 DO", "IF p_n = 1 THEN", "BREAK;", "ELSEIF p_n = 2 THEN",
                     "ELSE", "END IF;", "END WHILE;"):
            self.assertIn(frag, sql)

    def test_try_catch_throw(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          BEGIN TRY SELECT 1 END TRY
          BEGIN CATCH DECLARE @m NVARCHAR(10) = ERROR_MESSAGE(); THROW; END CATCH END""")
        self.assertIn("EXCEPTION WHEN ERROR THEN", sql)
        self.assertIn("@@error.message", sql)
        self.assertIn("RAISE;", sql)

    def test_string_concat_and_n_prefix(self):
        sql, _ = convert("CREATE PROC P AS BEGIN DECLARE @s VARCHAR(10) = N'a'; SET @s = @s + 'b' + CAST(1 AS VARCHAR(2)) END")
        self.assertIn("SET v_s = 'a';", sql)
        self.assertIn("v_s || 'b' || CAST(1 AS STRING)", sql)

    def test_select_assignment_single_and_multi(self):
        sql, _ = convert("CREATE PROC P AS BEGIN DECLARE @a INT, @b INT; SELECT @a = MIN(x), @b = MAX(x) FROM dbo.T; SELECT @a = 1 END")
        self.assertIn("SET (v_a, v_b) = (", sql)
        self.assertIn("SELECT AS STRUCT", sql)
        self.assertIn("SET v_a = 1;", sql)

    def test_tables_are_qualified_and_temp_tables(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          SELECT a INTO #t FROM dbo.Orders;
          SELECT * FROM #t;
          DROP TABLE #t END""")
        self.assertIn("CREATE OR REPLACE TEMP TABLE tmp_t AS", sql)
        self.assertIn("`proj.ds.Orders`", sql)
        self.assertIn("FROM tmp_t", sql)
        self.assertIn("DROP TABLE IF EXISTS tmp_t;", sql)

    def test_update_from_join_and_delete_join(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          UPDATE o SET o.a = b.a FROM dbo.Orders o JOIN dbo.B b ON b.id = o.id WHERE b.z = 1;
          DELETE o FROM dbo.Orders o JOIN dbo.B b ON b.id = o.id;
          DELETE FROM dbo.X;
          END""")
        self.assertIn("UPDATE `proj.ds.Orders` AS o SET a = b.a", sql)
        self.assertIn("b.id = o.id AND b.z = 1", sql)
        self.assertIn("DELETE FROM `proj.ds.Orders` AS o", sql)
        self.assertIn("EXISTS(", sql)
        self.assertIn("DELETE FROM `proj.ds.X`\n  WHERE\n    TRUE;", sql)

    def test_exec_resolves_named_args_and_defaults(self):
        src = """CREATE PROC dbo.Callee @a INT, @b INT = 5, @c INT = 7 AS SELECT 1
        GO
        CREATE PROC dbo.Caller AS EXEC dbo.Callee @c = 1, @a = 2"""
        sql, _ = convert(src)
        self.assertIn("CALL `proj.ds.Callee`(2, 5, 1);", sql)

    def test_dynamic_sql_and_cursor_are_flagged(self):
        sql, warns = convert("CREATE PROC P @n VARCHAR(5) AS BEGIN EXEC('SELECT ' + @n); DECLARE c CURSOR FOR SELECT 1 END")
        self.assertIn("EXECUTE IMMEDIATE", sql)
        self.assertIn("TODO(manual): cursor", sql)
        self.assertTrue(any(w.level == "review" for w in warns))

    def test_rowcount_and_transactions(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          DECLARE @n INT; BEGIN TRAN; UPDATE dbo.T SET a = 1; SET @n = @@ROWCOUNT; COMMIT; END""")
        self.assertIn("BEGIN TRANSACTION;", sql)
        self.assertIn("COMMIT TRANSACTION;", sql)
        self.assertIn("SET v_n = @@row_count;", sql)
        self.assertIn("WHERE\n    TRUE;", sql)

    def test_schema_mode(self):
        sql, _ = convert("CREATE PROC hr.P AS SELECT * FROM hr.T", schema_mode="drop")
        self.assertIn("`proj.ds.P`", sql)
        self.assertIn("`proj.ds.T`", sql)
        sql, _ = convert("CREATE PROC hr.P AS SELECT * FROM hr.T")
        self.assertIn("`proj.ds.hr_P`", sql)
        self.assertIn("`proj.ds.hr_T`", sql)

    def test_noop_set_statements_removed_and_empty_branch(self):
        sql, _ = convert("CREATE PROC P @a INT AS BEGIN SET NOCOUNT ON; IF @a = 1 PRINT 'x' END")
        self.assertIn("-- [removed] SET NOCOUNT ON", sql)
        self.assertIn("ASSERT TRUE;", sql)


class FixTests(unittest.TestCase):
    """Regression tests for the second round of fixes."""

    def test_cte_before_update_is_inlined(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          ;WITH r AS (SELECT a, MAX(d) AS m FROM dbo.H GROUP BY a)
          UPDATE h SET h.x = 1 FROM dbo.H h JOIN r ON r.a = h.a AND r.m = h.d;
          END""")
        self.assertNotIn("WITH r AS", sql)
        self.assertIn("UPDATE `proj.ds.H` AS h SET x = 1", sql)
        self.assertIn("FROM (", sql)
        self.assertIn(") AS r", sql)

    def test_cte_before_delete_join_and_chained_ctes(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          WITH a AS (SELECT id FROM dbo.T), b AS (SELECT id FROM a)
          DELETE x FROM dbo.X x JOIN b ON b.id = x.id;
          END""")
        self.assertNotIn("WITH a", sql)
        self.assertIn("DELETE FROM `proj.ds.X` AS x", sql)
        self.assertIn("`proj.ds.T`", sql)

    def test_datetime_functions(self):
        sql, _ = convert("CREATE PROC P AS BEGIN SELECT GETDATE() AS a, SYSDATETIME() AS b, GETUTCDATE() AS c END")
        self.assertIn("CURRENT_DATETIME() AS a", sql)
        self.assertIn("CURRENT_DATETIME() AS b", sql)
        self.assertIn("CURRENT_DATETIME('UTC') AS c", sql)
        sql, _ = convert("CREATE PROC P AS SELECT GETDATE() AS a", datetime_mode="timestamp")
        self.assertIn("CURRENT_TIMESTAMP() AS a", sql)

    def test_datediff_and_dateadd(self):
        sql, _ = convert("""CREATE PROC P @d DATE, @t DATETIME AS BEGIN
          SELECT DATEDIFF(day, @d, GETDATE()) AS a,
                 DATEADD(day, 1, @d) AS b,
                 DATEADD(day, 1, @t) AS c,
                 DATEADD(hour, 1, @d) AS e
          END""")
        self.assertIn("DATETIME_DIFF(CURRENT_DATETIME(), CAST(p_d AS DATETIME), DAY)", sql)
        self.assertIn("DATE_ADD(p_d, INTERVAL 1 DAY)", sql)
        self.assertIn("DATETIME_ADD(p_t, INTERVAL 1 DAY)", sql)
        self.assertIn("DATETIME_ADD(CAST(p_d AS DATETIME), INTERVAL 1 HOUR)", sql)

    def test_output_argument_gets_a_variable(self):
        src = """CREATE PROC dbo.Callee @a INT, @o DECIMAL(18,2) OUTPUT AS SET @o = @a
        GO
        CREATE PROC dbo.Caller AS BEGIN EXEC dbo.Callee @a = 1, @o = NULL; END"""
        sql, _ = convert(src)
        self.assertIn("DECLARE v_out0_o NUMERIC;", sql)
        self.assertIn("SET v_out0_o = NULL;", sql)
        self.assertIn("CALL `proj.ds.Callee`(1, v_out0_o);", sql)

    def test_output_argument_with_real_variable_is_passed_through(self):
        src = """CREATE PROC dbo.Callee @a INT, @o INT OUTPUT AS SET @o = @a
        GO
        CREATE PROC dbo.Caller AS BEGIN DECLARE @r INT; EXEC dbo.Callee 1, @r OUTPUT; END"""
        sql, _ = convert(src)
        self.assertIn("CALL `proj.ds.Callee`(1, v_r);", sql)

    def test_out_param_mode(self):
        sql, _ = convert("CREATE PROC P @o INT OUTPUT AS SET @o = 1", out_param_mode="OUT")
        self.assertIn("OUT p_o INT64", sql)
        self.assertNotIn("INOUT", sql)

    def test_bit_variables_use_booleans(self):
        sql, _ = convert("""CREATE PROC P @f BIT = 1 AS BEGIN
          DECLARE @g BIT = 0; SET @g = 1; IF @g = 1 AND @f = 0 SELECT 1 END""")
        self.assertIn("SET v_g = FALSE;", sql)
        self.assertIn("SET v_g = TRUE;", sql)
        self.assertIn("v_g = TRUE AND p_f = FALSE", sql)

    def test_bit_columns_from_config(self):
        sql, warns = convert("""CREATE PROC P AS BEGIN
          UPDATE dbo.U SET IsActive = 1 WHERE IsActive = 0;
          INSERT INTO dbo.U (Name, IsActive) VALUES ('a', 1);
          END""", bit_columns=["IsActive"])
        self.assertIn("IsActive = TRUE", sql)
        self.assertIn("IsActive = FALSE", sql)
        self.assertIn("('a', TRUE)", sql)
        self.assertFalse(any(w.level == "review" for w in warns))

    def test_unconfigured_bit_like_column_is_flagged(self):
        _, warns = convert("CREATE PROC P AS SELECT * FROM dbo.U WHERE IsActive = 1")
        self.assertTrue(any(w.level == "review" and "IsActive" in w.message for w in warns))

    def test_cursor_loop_becomes_for_loop(self):
        sql, warns = convert("""CREATE PROC P @pre VARCHAR(5) AS BEGIN
          DECLARE @id INT, @nm VARCHAR(20);
          DECLARE c CURSOR LOCAL FAST_FORWARD FOR SELECT Id, @pre + Name FROM dbo.C ORDER BY Id;
          OPEN c;
          FETCH NEXT FROM c INTO @id, @nm;
          WHILE @@FETCH_STATUS = 0
          BEGIN
            INSERT INTO dbo.L (a, b) VALUES (@id, @nm);
            FETCH NEXT FROM c INTO @id, @nm;
          END
          CLOSE c; DEALLOCATE c;
          END""")
        self.assertIn("FOR rec_c IN (", sql)
        self.assertIn("p_pre || Name AS col2", sql)
        self.assertIn("SET v_id = rec_c.Id;", sql)
        self.assertIn("SET v_nm = rec_c.col2;", sql)
        self.assertIn("END FOR;", sql)
        self.assertNotIn("@@FETCH_STATUS", sql.replace("--", ""))   # only in comments, if at all
        self.assertIn("IN p_pre STRING", sql)                       # parameter not shadowed by cursor parsing
        self.assertFalse(any(w.level == "review" for w in warns))

    def test_unrecognised_cursor_pattern_is_still_flagged(self):
        sql, warns = convert("CREATE PROC P AS BEGIN DECLARE c CURSOR FOR SELECT 1; OPEN c; END")
        self.assertIn("TODO(manual)", sql)
        self.assertTrue(any(w.level == "review" for w in warns))

    def test_constant_dynamic_sql_is_converted(self):
        sql, _ = convert("""CREATE PROC P @d DATE AS BEGIN
          EXEC sp_executesql N'SELECT TOP 5 a FROM dbo.T WHERE d > @d', N'@d DATE', @d = @d; END""")
        self.assertIn('EXECUTE IMMEDIATE r"""SELECT', sql)
        self.assertIn("`proj.ds.T`", sql)
        self.assertIn("LIMIT 5", sql)
        self.assertIn("USING p_d AS d;", sql)

    def test_aggregate_select_assignment_has_no_limit(self):
        sql, _ = convert("CREATE PROC P AS BEGIN DECLARE @n INT; SELECT @n = COUNT(*) FROM dbo.T; END")
        self.assertNotIn("LIMIT", sql)
        sql, _ = convert("CREATE PROC P AS BEGIN DECLARE @n INT; SELECT @n = Id FROM dbo.T ORDER BY Id; END")
        self.assertIn("LIMIT 1", sql)

    def test_table_variable_uses_temp_prefix_and_is_replaceable(self):
        sql, _ = convert("CREATE PROC P AS BEGIN DECLARE @t TABLE (id INT); INSERT INTO @t VALUES (1); SELECT * FROM @t END")
        self.assertIn("CREATE OR REPLACE TEMP TABLE tmp_t (", sql)
        self.assertIn("INSERT INTO tmp_t", sql)


class ViewTests(unittest.TestCase):
    def view(self, src, **cfg):
        config = Config(project="proj", default_dataset="ds", **cfg)
        v = parse_view_batch(src)
        return Translator(config).convert_view(v, "ds")

    def test_simple_view(self):
        sql, _, full = self.view("CREATE VIEW dbo.vw AS SELECT TOP 3 a, ISNULL(b, 0) AS b FROM dbo.T WITH (NOLOCK) ORDER BY a")
        self.assertEqual(full, "proj.ds.vw")
        self.assertIn("CREATE OR REPLACE VIEW `proj.ds.vw` AS", sql)
        self.assertIn("COALESCE(b, 0) AS b", sql)
        self.assertIn("LIMIT 3", sql)
        self.assertIn("`proj.ds.T`", sql)
        self.assertNotIn("NOLOCK", sql)

    def test_view_with_columns_options_cte_and_check_option(self):
        sql, warns, _ = self.view("""CREATE OR ALTER VIEW [hr].[v] (x, y) WITH SCHEMABINDING AS
        WITH c AS (SELECT a, b FROM hr.T) SELECT a, b FROM c WHERE a > 0 WITH CHECK OPTION;""")
        self.assertIn("CREATE OR REPLACE VIEW `proj.ds.hr_v` (x, y) AS", sql)
        self.assertIn("WITH c AS (", sql)
        self.assertNotIn("CHECK OPTION", sql)
        self.assertTrue(any("options dropped" in w.message for w in warns))

    def test_non_view_returns_none(self):
        self.assertIsNone(parse_view_batch("CREATE PROC P AS SELECT 1"))


class FolderTests(unittest.TestCase):
    def test_views_and_procedures_in_one_run_with_deploy_order(self):
        import tempfile
        from tsql2bq.converter import convert_path, write_report
        with tempfile.TemporaryDirectory() as d:
            src, out = Path(d) / "in" / "salesdb", Path(d) / "out"
            src.mkdir(parents=True)
            (src / "a.sql").write_text(
                "CREATE VIEW dbo.vw_top AS SELECT * FROM dbo.vw_base\nGO\n"
                "CREATE VIEW dbo.vw_base AS SELECT a FROM dbo.T\nGO\n"
                "CREATE PROC dbo.P AS SELECT * FROM dbo.vw_top\nGO\n", encoding="utf-8")
            cfg = Config(project="proj", default_dataset="ds", dataset_map={"salesdb": "sales"})
            results = convert_path(Path(d) / "in", out, cfg)
            write_report(results, out, cfg)
            self.assertEqual({r.kind for r in results}, {"view", "procedure"})
            order = (out / "deploy_order.txt").read_text().split()
            self.assertEqual([Path(o).name for o in order], ["vw_base.sql", "vw_top.sql", "P.sql"])
            self.assertTrue((out / "deploy_all.sql").exists())


class IntegerDivisionTests(unittest.TestCase):
    SRC = "CREATE PROC dbo.P @a INT, @d DECIMAL(10,2) AS SELECT @a/2 AS i, @a/@a/3 AS j, @d/2 AS n, @a/2.0 AS m, Amt/Qty AS u FROM dbo.T"

    def test_int_division_uses_div(self):
        sql, _ = convert(self.SRC)
        self.assertIn("DIV(p_a, 2) AS i", sql)
        self.assertIn("DIV(DIV(p_a, p_a), 3) AS j", sql)

    def test_decimal_division_unchanged(self):
        sql, _ = convert(self.SRC)
        self.assertIn("p_d / 2 AS n", sql)
        self.assertIn("p_a / 2.0 AS m", sql)

    def test_unknown_types_flagged_and_column_types_resolve_it(self):
        sql, warns = convert(self.SRC)
        self.assertTrue(any("division" in w.message and w.level == "review" for w in warns))
        sql, warns = convert(self.SRC, column_types={"Amt": "int", "Qty": "int"})
        self.assertIn("DIV(Amt, Qty) AS u", sql)
        self.assertFalse(any("division" in w.message for w in warns))


class StringCaseTests(unittest.TestCase):
    SRC = "CREATE PROC dbo.P AS SELECT 1 FROM dbo.T WHERE Status = 'new' AND Code IN ('a','B') AND Nm LIKE 'ab%' AND D >= '2026-01-01'"

    def test_warn_mode_flags_each_column_once(self):
        _, warns = convert(self.SRC)
        flagged = [w for w in warns if "case-sensitive" in w.message]
        self.assertEqual(len(flagged), 3)          # Status, Code, Nm - not the date literal

    def test_lower_mode_wraps_both_sides(self):
        sql, warns = convert(self.SRC, case_sensitivity="lower")
        self.assertIn("LOWER(Status) = LOWER('new')", sql)
        self.assertIn("LOWER(Code) IN (LOWER('a'), LOWER('B'))", sql)
        self.assertIn("LOWER(Nm) LIKE LOWER('ab%')", sql)
        self.assertIn("D >= '2026-01-01'", sql)
        self.assertFalse(any("case-sensitive" in w.message for w in warns))

    def test_off_mode(self):
        sql, warns = convert(self.SRC, case_sensitivity="off")
        self.assertIn("Status = 'new'", sql)
        self.assertFalse(any("case-sensitive" in w.message for w in warns))


class FixesFromSampleRunTests(unittest.TestCase):
    """Regressions found when comparing the tool's output with the expected BigQuery scripts."""

    def test_error_functions_in_catch(self):
        sql, _ = convert("""CREATE PROC sales.usp_X AS BEGIN
          BEGIN TRY SELECT 1; END TRY
          BEGIN CATCH
            INSERT INTO audit.ErrorLog (N, M, P, L) VALUES (ERROR_NUMBER(), ERROR_MESSAGE(), ERROR_PROCEDURE(), ERROR_LINE());
            SELECT ERROR_NUMBER() AS ErrorNumber;
            THROW;
          END CATCH END""")
        body = "\n".join(l for l in sql.splitlines() if not l.strip().startswith("--"))
        self.assertNotIn("ERROR_NUMBER()", body)
        self.assertIn("CAST(NULL AS INT64) AS ErrorNumber", sql)
        self.assertIn("@@error.message", sql)
        self.assertIn("'usp_X'", sql)                      # not @@error.stack_trace (that is an ARRAY)
        # v5: ERROR_LINE() is the INT64 line of the first stack frame; the raw ARRAY must never be used as a value
        self.assertIn("@@error.stack_trace[SAFE_OFFSET(0)].line", body)
        self.assertNotIn("@@error.stack_trace,", body)
        self.assertNotIn("ERROR_LINE()", body)

    def test_year_month_day_become_extract(self):
        sql, _ = convert("CREATE PROC P @d DATE AS SELECT 1 FROM T WHERE YEAR(SaleDate) = YEAR(@d) "
                         "AND MONTH(SaleDate) = MONTH(@d) AND DAY(SaleDate) = 1 AND YEAR('2020-01-01') = 2020")
        self.assertIn("EXTRACT(YEAR FROM SaleDate) = EXTRACT(YEAR FROM p_d)", sql)
        self.assertIn("EXTRACT(MONTH FROM SaleDate) = EXTRACT(MONTH FROM p_d)", sql)
        self.assertIn("EXTRACT(DAY FROM SaleDate) = 1", sql)
        self.assertIn("EXTRACT(YEAR FROM CAST('2020-01-01' AS DATE))", sql)
        self.assertNotIn("YEAR(", sql)

    def test_dynamic_sql_in_variable_is_converted(self):
        sql, warns = convert("""CREATE PROC P @RegionId INT AS BEGIN
          DECLARE @Sql NVARCHAR(MAX);
          SET @Sql = N'UPDATE sales.RegionStatistics SET LastProcessedDate = GETDATE() WHERE RegionId = @RegionId';
          EXEC sp_executesql @Sql, N'@RegionId INT', @RegionId;
        END""")
        self.assertIn('SET v_Sql = r"""UPDATE `proj.ds.sales_RegionStatistics` SET LastProcessedDate = CURRENT_DATETIME()', sql)
        self.assertIn("EXECUTE IMMEDIATE v_Sql USING p_RegionId AS RegionId;", sql)
        self.assertFalse(any("still T-SQL" in w.message for w in warns))

    def test_dynamic_sql_built_by_concatenation_is_still_flagged(self):
        sql, warns = convert("""CREATE PROC P @t NVARCHAR(50) AS BEGIN
          DECLARE @Sql NVARCHAR(MAX) = N'SELECT * FROM ';
          SET @Sql = @Sql + @t;
          EXEC sp_executesql @Sql;
        END""")
        self.assertTrue(any("still T-SQL" in w.message for w in warns))

    def test_top_variable_becomes_qualify(self):
        sql, _ = convert("CREATE PROC P @n INT AS SELECT TOP (@n) a FROM T WHERE b = 1 ORDER BY a DESC")
        self.assertIn("QUALIFY", sql)
        self.assertIn("ROW_NUMBER() OVER (ORDER BY a DESC) <= p_n", sql)
        self.assertNotIn("LIMIT p_n", sql)

    def test_top_constant_stays_limit(self):
        sql, _ = convert("CREATE PROC P AS SELECT TOP 5 a FROM T ORDER BY a")
        self.assertIn("LIMIT 5", sql)
        self.assertNotIn("QUALIFY", sql)

    def test_temp_table_inside_transaction_uses_plain_create(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          CREATE TABLE #a (x INT);
          BEGIN TRANSACTION;
          CREATE TABLE #b (x INT);
          COMMIT TRANSACTION;
        END""")
        self.assertIn("CREATE OR REPLACE TEMP TABLE tmp_a", sql)
        self.assertIn("CREATE TEMP TABLE tmp_b", sql)
        self.assertNotIn("CREATE OR REPLACE TEMP TABLE tmp_b", sql)

    def test_update_from_join_notes_duplicate_match_risk(self):
        _, warns = convert("CREATE PROC P AS UPDATE o SET o.X = s.X FROM dbo.Orders o JOIN #S s ON s.Id = o.Id")
        self.assertTrue(any("more than one source row" in w.message for w in warns))


if __name__ == "__main__":
    unittest.main()
