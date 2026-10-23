"""Regression tests for the edge cases fixed in v4 (each one produced wrong or invalid BigQuery before)."""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from tsql2bq.converter import convert_path  # noqa: E402
from tsql2bq.parser import parse_batch, split_batches  # noqa: E402
from tsql2bq.translator import Config, Translator  # noqa: E402
from tsql2bq.views import parse_view_batch  # noqa: E402


def convert(src, **cfg):
    config = Config(project="proj", default_dataset="ds", **cfg)
    procs = [p for p in (parse_batch(b) for b in split_batches(src)) if p]
    reg = {f"{(p.schema or 'dbo').lower()}.{p.name.lower()}": p for p in procs}
    sql, warns, _ = Translator(config).convert(procs[-1], "ds", reg)
    return sql, warns


def flat(sql):
    """Generated SQL without comment lines, whitespace collapsed."""
    return " ".join(" ".join(l for l in sql.splitlines() if not l.lstrip().startswith("--")).split())


def has(warns, text, level=None):
    return any(text in w.message and (level is None or w.level == level) for w in warns)


class ParserEdgeTests(unittest.TestCase):
    def test_nested_block_comment(self):
        sql, _ = convert("CREATE PROC P AS BEGIN /* a /* b */ still comment */ SELECT 2; END")
        self.assertNotIn("still AS comment", sql)
        self.assertNotIn("TODO", sql)

    def test_go_inside_string_does_not_split(self):
        batches = split_batches("CREATE PROC P AS SELECT 'x\nGO\ny' AS a\nGO\nSELECT 1")
        self.assertEqual(len(batches), 2)

    def test_drop_table_if_exists_is_one_statement(self):
        sql, warns = convert("CREATE PROC P AS BEGIN DROP TABLE IF EXISTS #x; TRUNCATE TABLE dbo.T; END")
        self.assertIn("DROP TABLE IF EXISTS tmp_x;", sql)
        self.assertNotIn("IF TRUE", sql)
        self.assertFalse(has(warns, "could not be converted"))

    def test_money_literal(self):
        sql, _ = convert("CREATE PROC P AS BEGIN DECLARE @m MONEY = $12.50; SELECT @m; END")
        self.assertIn("SET v_m = 12.50;", sql)

    def test_cursor_variable_does_not_leak_a_select(self):
        sql, warns = convert("""CREATE PROC P AS BEGIN DECLARE @c CURSOR;
          SET @c = CURSOR FOR SELECT id FROM dbo.T; OPEN @c; END""")
        self.assertNotIn("\n  SELECT", sql)
        self.assertTrue(has(warns, "cursor variables"))

    def test_trailing_create_does_not_crash(self):
        self.assertIsNone(parse_batch("CREATE"))


class CursorTests(unittest.TestCase):
    BODY = """CREATE PROC P AS BEGIN DECLARE @id INT;
      DECLARE cur CURSOR FOR SELECT id FROM dbo.T;
      OPEN cur; FETCH NEXT FROM cur INTO @id;
      WHILE {cond}
      BEGIN
        IF @id = 3 BEGIN FETCH NEXT FROM cur INTO @id; CONTINUE; END
        INSERT INTO dbo.L (id) VALUES (@id);
        FETCH NEXT FROM cur INTO @id;
      END
      CLOSE cur; DEALLOCATE cur; END"""

    def test_parenthesised_and_minus_one_fetch_status(self):
        for cond in ("(@@FETCH_STATUS = 0)", "@@FETCH_STATUS <> -1"):
            sql, warns = convert(self.BODY.format(cond=cond))
            self.assertIn("FOR rec_cur IN (", sql, cond)
            self.assertFalse(has(warns, "FETCH is not supported"), cond)
            self.assertIn("CONTINUE;", sql)

    def test_union_cursor(self):
        sql, _ = convert(self.BODY.format(cond="@@FETCH_STATUS = 0").replace(
            "SELECT id FROM dbo.T;", "SELECT id FROM dbo.T UNION SELECT id FROM dbo.S;"))
        self.assertIn("FOR rec_cur IN (", sql)


class SemanticTests(unittest.TestCase):
    def test_declare_in_loop_keeps_value(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN DECLARE @i INT = 0;
          WHILE @i < 3 BEGIN DECLARE @acc INT; SET @acc = ISNULL(@acc, 0) + @i; SET @i += 1; END END""")
        self.assertNotIn("SET v_acc = NULL;", sql)

    def test_rowcount_after_select_assignment_is_flagged(self):
        _, warns = convert("""CREATE PROC P @id INT AS BEGIN DECLARE @n VARCHAR(5);
          SELECT @n = name FROM dbo.T WHERE id = @id; IF @@ROWCOUNT = 0 RETURN; END""")
        self.assertTrue(has(warns, "@@ROWCOUNT is read after", "review"))

    def test_rowcount_after_update_is_not_flagged(self):
        _, warns = convert("CREATE PROC P AS BEGIN UPDATE dbo.T SET v = 1; IF @@ROWCOUNT = 0 RETURN; END")
        self.assertFalse(has(warns, "@@ROWCOUNT is read after"))

    def test_trancount_guard_is_kept_as_flag(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN BEGIN TRY
            SELECT 1; BEGIN TRAN; UPDATE dbo.T SET v = 1; COMMIT;
          END TRY BEGIN CATCH IF @@TRANCOUNT > 0 ROLLBACK; THROW; END CATCH END""")
        self.assertIn("DECLARE v_in_transaction BOOL DEFAULT FALSE;", sql)
        self.assertIn("SET v_in_transaction = TRUE;", sql)
        self.assertIn("IF v_in_transaction THEN", sql)

    def test_xact_state(self):
        sql, warns = convert("""CREATE PROC P AS BEGIN TRY SELECT 1; END TRY
          BEGIN CATCH IF XACT_STATE() <> 0 ROLLBACK; END CATCH""")
        self.assertNotIn("XACT_STATE()", sql)
        self.assertIn("IF v_in_transaction THEN", sql)

    def test_rollback_to_savepoint_is_not_full_rollback(self):
        sql, warns = convert("CREATE PROC P AS BEGIN BEGIN TRAN; SAVE TRAN sp1; ROLLBACK TRAN sp1; COMMIT; END")
        self.assertEqual(sql.count("ROLLBACK TRANSACTION;"), 0)
        self.assertTrue(has(warns, "ROLLBACK to a savepoint"))

    def test_select_accumulation_is_flagged(self):
        _, warns = convert("CREATE PROC P AS BEGIN DECLARE @s VARCHAR(MAX) = ''; SELECT @s = @s + name FROM dbo.T; END")
        self.assertTrue(has(warns, "accumulates over ALL rows", "review"))

    def test_exec_return_status(self):
        sql, warns = convert("""CREATE PROC dbo.C AS SELECT 1
        GO
        CREATE PROC dbo.P AS BEGIN DECLARE @rc INT; EXEC @rc = dbo.C; END""")
        self.assertIn("SET v_rc = 0;", sql)
        sql, warns = convert("""CREATE PROC dbo.C AS RETURN -1
        GO
        CREATE PROC dbo.P AS BEGIN DECLARE @rc INT; EXEC @rc = dbo.C; END""")
        self.assertTrue(has(warns, "RETURN status", "review"))

    def test_semantic_set_options_flagged(self):
        _, warns = convert("CREATE PROC P AS BEGIN SET DATEFIRST 1; SET ANSI_NULLS OFF; SET NOCOUNT ON; END")
        self.assertTrue(has(warns, "SET DATEFIRST removed", "review"))
        self.assertTrue(has(warns, "SET ANSI_NULLS removed", "review"))
        _, warns = convert("CREATE PROC P AS BEGIN SET DATEFIRST 7; SET ANSI_NULLS ON; END")
        self.assertFalse(any(w.level == "review" for w in warns))

    def test_raiserror_with_message_number(self):
        sql, warns = convert("CREATE PROC P AS RAISERROR(50001, 16, 1)")
        self.assertIn("RAISE USING MESSAGE = 'Error 50001';", sql)

    def test_permanent_ddl_inside_transaction(self):
        _, warns = convert("CREATE PROC P AS BEGIN BEGIN TRAN; SELECT * INTO dbo.Copy FROM dbo.T; COMMIT; END")
        self.assertTrue(has(warns, "permanent table DDL inside a transaction", "review"))


class DmlTests(unittest.TestCase):
    def test_update_target_not_first_in_from(self):
        sql, _ = convert("CREATE PROC P AS UPDATE t SET v = s.v FROM dbo.S s JOIN dbo.T t ON s.id = t.id")
        self.assertIn("UPDATE `proj.ds.T` AS t SET v = s.v", flat(sql))
        self.assertIn("FROM `proj.ds.S` AS s WHERE s.id = t.id", flat(sql))
        self.assertNotIn("`proj.ds.t`", sql)

    def test_update_left_join_becomes_subquery(self):
        sql, _ = convert("CREATE PROC P AS UPDATE t SET t.v = s.v FROM dbo.T t LEFT JOIN dbo.S s ON s.id = t.id")
        f = flat(sql)
        self.assertIn("UPDATE `proj.ds.T` AS t SET v = ( SELECT s.v FROM `proj.ds.S` AS s WHERE s.id = t.id )", f)
        self.assertNotIn("LEFT JOIN", f)

    def test_update_alias_without_join(self):
        sql, _ = convert("CREATE PROC P AS UPDATE t SET t.v = 1 FROM dbo.T t WHERE t.k = 2")
        self.assertIn("UPDATE `proj.ds.T` AS t SET v = 1 WHERE t.k = 2", flat(sql))

    def test_update_right_join_is_todo_not_wrong_sql(self):
        sql, warns = convert("CREATE PROC P AS UPDATE t SET v = 1 FROM dbo.T t RIGHT JOIN dbo.S s ON s.id = t.id")
        self.assertIn("TODO(manual)", sql)
        self.assertNotIn("`proj.ds.t`", sql)

    def test_delete_alias(self):
        sql, _ = convert("CREATE PROC P AS DELETE t FROM dbo.T t WHERE t.v = 0")
        self.assertIn("DELETE FROM `proj.ds.T` AS t WHERE t.v = 0;", flat(sql))

    def test_delete_from_joined_table_deletes_the_named_table(self):
        sql, _ = convert("CREATE PROC P AS DELETE s FROM dbo.T t JOIN dbo.S s ON s.id = t.id WHERE t.x = 1")
        f = flat(sql)
        self.assertIn("DELETE FROM `proj.ds.S` AS s WHERE EXISTS(", f)
        self.assertIn("FROM `proj.ds.T` AS t WHERE s.id = t.id AND t.x = 1", f)

    def test_delete_anti_join(self):
        sql, _ = convert("CREATE PROC P AS DELETE t FROM dbo.T t LEFT JOIN dbo.S s ON s.id = t.id WHERE s.id IS NULL")
        self.assertIn("WHERE NOT EXISTS(", flat(sql))

    def test_update_delete_top_are_todo(self):
        sql, warns = convert("CREATE PROC P AS BEGIN DELETE TOP (10) FROM dbo.T WHERE v = 0; "
                             "UPDATE TOP (5) dbo.T SET v = 1; END")
        self.assertNotIn("`proj.ds.TOP`", sql)
        self.assertEqual(sql.count("TODO(manual)"), 2)

    def test_update_set_variable_is_todo(self):
        sql, _ = convert("CREATE PROC P AS BEGIN DECLARE @x INT; UPDATE dbo.T SET @x = v WHERE id = 1; END")
        self.assertIn("TODO(manual)", sql)

    def test_offset_fetch_with_variables(self):
        sql, _ = convert("CREATE PROC P @p INT, @s INT AS "
                         "SELECT id FROM dbo.T ORDER BY id OFFSET (@p - 1) * @s ROWS FETCH NEXT @s ROWS ONLY")
        self.assertIn("QUALIFY", sql)
        self.assertNotIn("LIMIT p_s", sql)
        self.assertNotIn("OFFSET", flat(sql))

    def test_group_by_with_rollup(self):
        sql, _ = convert("CREATE PROC P AS SELECT a, SUM(c) FROM dbo.T GROUP BY a WITH ROLLUP")
        self.assertIn("ROLLUP (", sql)
        self.assertNotIn("WITH ROLLUP", sql)


class ExpressionTests(unittest.TestCase):
    def test_case_lower_mode_leaves_set_assignments(self):
        sql, _ = convert("CREATE PROC P AS UPDATE dbo.T SET Status = 'Done' WHERE Status = 'New'",
                         case_sensitivity="lower")
        self.assertIn("SET Status = 'Done'", sql)
        self.assertIn("LOWER(Status) = LOWER('New')", sql)

    def test_case_warn_mode_ignores_set_assignments(self):
        _, warns = convert("CREATE PROC P AS UPDATE dbo.T SET Status = 'Done' WHERE id = 1")
        self.assertFalse(has(warns, "compared with a text literal"))

    def test_bits_in_insert_select_case_and_in(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN
          INSERT INTO dbo.T (IsActive, Name) SELECT 1, 'a';
          UPDATE dbo.T SET IsActive = CASE WHEN x > 0 THEN 1 ELSE 0 END;
          SELECT * FROM dbo.T WHERE IsActive IN (1); END""", bit_columns=["IsActive"])
        f = flat(sql)
        self.assertIn("SELECT TRUE, 'a'", f)
        self.assertIn("THEN TRUE ELSE FALSE END", f)
        self.assertIn("IsActive IN (TRUE)", f)

    def test_bool_var_compared_with_true_string(self):
        sql, _ = convert("CREATE PROC P @f BIT AS IF @f = 'true' SELECT 1")
        self.assertIn("IF p_f = TRUE THEN", sql)

    def test_functions(self):
        sql, warns = convert("""CREATE PROC P @s VARCHAR(20), @n INT, @d DATETIME AS SELECT
          ROUND(@n, 2, 1) AS r, CONVERT(INT, @s) AS c, CAST(@s AS VARCHAR(5)) AS v, CONCAT(@s, @n) AS cc,
          ISNULL(@n, '') AS i, LEN(@s) AS l, DATALENGTH(@s) AS dl, AVG(@n) AS a, @d + 1 AS d1,
          DATEADD(day, DATEDIFF(day, 0, @d), 0) AS t, CONVERT(DATETIME, '2020-01-01', 120) AS p,
          STUFF(@s, 1, 2, 'ab') AS st, PATINDEX('%x%', @s) AS pi""")
        f = flat(sql)
        for frag in ("TRUNC(p_n, 2) AS r", "CAST(p_s AS INT64) AS c", "SUBSTRING(CAST(p_s AS STRING), 1, 5) AS v",
                     "COALESCE(CAST(p_n AS STRING), '')", "COALESCE(p_n, 0) AS i",
                     "LENGTH(RTRIM(CAST(p_s AS STRING), ' ')) AS l", "BYTE_LENGTH(p_s) AS dl",
                     "CAST(TRUNC(AVG(p_n)) AS INT64) AS a", "DATETIME_ADD(p_d, INTERVAL 1 DAY) AS d1",
                     "DATETIME_TRUNC(p_d, DAY) AS t", "CAST('2020-01-01' AS DATETIME) AS p",
                     "SUBSTRING(p_s, 1, 1 - 1) || 'ab' || SUBSTRING(p_s, 1 + 2)"):
            self.assertIn(frag, f)
        self.assertNotIn("SAFE_CAST", f)
        self.assertNotIn("PARSE_TIMESTAMP", f)
        self.assertIn("REGEXP_INSTR(p_s, '(?s)x') AS pi", f)        # v5: PATINDEX converted

    def test_like_character_classes_and_escape(self):
        sql, _ = convert(r"""CREATE PROC P AS SELECT * FROM dbo.T WHERE a LIKE '[0-9]%' OR b LIKE '%[_]%'
          OR c LIKE '10!%%' ESCAPE '!' OR d LIKE 'C:\t%'""")
        f = flat(sql)
        self.assertIn("REGEXP_CONTAINS(a, '(?s)^[0-9].*$')", f)
        self.assertIn(r"b LIKE '%\\_%'", f)
        self.assertIn(r"c LIKE '10\\%%'", f)
        self.assertIn(r"d LIKE 'C:\\\\t%'", f)
        self.assertNotIn("ESCAPE", f)

    def test_for_xml_path_idioms(self):
        sql, warns = convert("""CREATE PROC P AS BEGIN
          SELECT STUFF((SELECT ', ' + l.name FROM dbo.L l ORDER BY l.name FOR XML PATH('')), 1, 2, '') AS n;
          SELECT id FROM dbo.T FOR JSON AUTO; END""")
        f = flat(sql)
        self.assertIn("STRING_AGG(l.name, ', ' ORDER BY l.name)", f)
        self.assertNotIn("STUFF", f)
        # v5: single-table FOR JSON AUTO is the same as PATH -> TO_JSON_STRING(ARRAY(SELECT AS STRUCT ...))
        self.assertIn("TO_JSON_STRING(ARRAY(SELECT AS STRUCT id FROM", f)
        self.assertFalse(has(warns, "FOR JSON has no BigQuery equivalent"))

    def test_string_plus_number_is_numeric(self):
        sql, warns = convert("CREATE PROC P @s VARCHAR(5) AS SELECT @s + 1")
        self.assertIn("CAST(p_s AS INT64) + 1", sql)

    def test_collate(self):
        sql, _ = convert("CREATE PROC P AS SELECT * FROM dbo.T WHERE a COLLATE Latin1_General_CS_AS = 'x' "
                         "AND b COLLATE Latin1_General_CI_AS = 'y'")
        f = flat(sql)
        self.assertIn("a = 'x'", f)
        self.assertIn("COLLATE(b, 'und:ci') = 'y'", f)

    def test_udf_and_unknown_database(self):
        sql, warns = convert("CREATE PROC P AS SELECT dbo.fn_Tax(a) FROM OtherDb.dbo.T", dataset_map={"salesdb": "s"})
        self.assertIn("`proj.ds.fn_Tax`(a)", sql)
        self.assertTrue(has(warns, "user-defined function", "review"))
        self.assertTrue(has(warns, "database 'OtherDb' is not in config 'dataset_map'", "review"))

    def test_dynamic_sql_temp_table_and_multiple_statements(self):
        sql, _ = convert("""CREATE PROC P AS BEGIN CREATE TABLE #t (id INT);
          EXEC('SELECT * FROM #t'); EXEC('UPDATE dbo.T SET v = 1; DELETE FROM dbo.T WHERE v = 2'); END""")
        self.assertIn("FROM tmp_t", sql)
        self.assertNotIn("`proj.ds.t`", sql)
        self.assertEqual(flat(sql).count("EXECUTE IMMEDIATE"), 3)

    def test_top_100_percent_in_view(self):
        v = parse_view_batch("CREATE VIEW dbo.V AS SELECT TOP 100 PERCENT id FROM dbo.T ORDER BY id")
        sql, warns, _ = Translator(Config(project="proj", default_dataset="ds")).convert_view(v, "ds")
        self.assertNotIn("PERCENT", sql)


class OrchestrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        (self.tmp / "in" / "salesdb").mkdir(parents=True)
        (self.tmp / "in" / "hrdb").mkdir(parents=True)

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def run_conv(self):
        cfg = Config(project="proj", default_dataset="d0", dataset_map={"salesdb": "s", "hrdb": "h"})
        return convert_path(self.tmp / "in", self.tmp / "out", cfg)

    def test_same_procedure_name_in_two_databases(self):
        (self.tmp / "in/salesdb/a.sql").write_text("CREATE PROC dbo.usp_Log @m VARCHAR(9) AS SELECT @m")
        (self.tmp / "in/hrdb/b.sql").write_text(
            "CREATE PROC dbo.usp_Log @l INT, @m VARCHAR(9), @x INT = 0 AS SELECT @m\nGO\n"
            "CREATE PROC dbo.Caller AS EXEC dbo.usp_Log 1, 'x'")
        self.run_conv()
        out = (self.tmp / "out/hrdb/Caller.sql").read_text()
        self.assertIn("CALL `proj.h.usp_Log`(1, 'x', 0);", out)

    def test_name_collision_is_reported(self):
        (self.tmp / "in/salesdb/a.sql").write_text(
            "CREATE PROC sales.usp_X AS SELECT 1\nGO\nCREATE PROC dbo.sales_usp_X AS SELECT 2")
        res = self.run_conv()
        self.assertTrue(any(r.status == "failed" and "name collision" in r.error for r in res))

    def test_functions_and_triggers_are_reported(self):
        (self.tmp / "in/hrdb/f.sql").write_text(
            "CREATE FUNCTION dbo.fn (@a INT) RETURNS INT AS BEGIN RETURN @a END\nGO\n"
            "CREATE PROC dbo.P AS SELECT 1")
        res = self.run_conv()
        self.assertTrue(any(r.status == "skipped" and "CREATE FUNCTION" in r.error for r in res))
        self.assertTrue(any(r.status == "ok" and r.procedure == "dbo.P" for r in res))

    def test_routine_name_with_space(self):
        (self.tmp / "in/hrdb/f.sql").write_text("CREATE PROC dbo.[Get Orders] AS SELECT 1")
        self.run_conv()
        self.assertTrue((self.tmp / "out/hrdb/Get_Orders.sql").exists())


if __name__ == "__main__":
    unittest.main()
