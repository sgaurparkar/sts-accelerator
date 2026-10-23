"""Regression tests for v5: gaps found by comparing v4 with the BigQuery Migration Service on the 129-probe pack."""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from tsql2bq.converter import CONFIG_WARNINGS, convert_path, load_config, parse_table_type  # noqa: E402
from tsql2bq.parser import parse_batch, split_batches  # noqa: E402
from tsql2bq.translator import Config, Translator  # noqa: E402


def convert(src, **cfg):
    config = Config(project="proj", default_dataset="ds", **cfg)
    procs = [p for p in (parse_batch(b) for b in split_batches(src)) if p]
    reg = {f"{(p.schema or 'dbo').lower()}.{p.name.lower()}": p for p in procs}
    sql, warns, _ = Translator(config).convert(procs[-1], "ds", reg)
    return sql, warns


def flat(sql):
    """Generated SQL without comment lines; whitespace collapsed (also inside parentheses)."""
    out = " ".join(" ".join(l for l in sql.splitlines() if not l.lstrip().startswith("--")).split())
    return out.replace("( ", "(").replace(" )", ")")


def has(warns, text, level=None):
    return any(text in w.message and (level is None or w.level == level) for w in warns)


def proc(body, params=""):
    return f"CREATE PROC dbo.P {params} AS BEGIN {body} END"


class BugFixTests(unittest.TestCase):
    """v4 produced wrong output for these."""

    def test_try_convert_stays_safe(self):
        f = flat(convert(proc("SELECT TRY_CONVERT(INT, Name) AS a, CONVERT(INT, Name) AS b FROM dbo.T;"))[0])
        self.assertIn("SAFE_CAST(Name AS INT64) AS a", f)
        self.assertIn("CAST(Name AS INT64) AS b", f)
        self.assertNotIn("SAFE_CAST(Name AS INT64) AS b", f)

    def test_try_convert_date_style(self):
        f = flat(convert(proc("SELECT TRY_CONVERT(DATE, s, 103) AS d FROM dbo.T;"))[0])
        self.assertIn("SAFE.PARSE_DATE('%d/%m/%Y', s)", f)

    def test_xact_state_in_catch_is_minus_one(self):
        sql, _ = convert(proc("BEGIN TRY BEGIN TRAN; UPDATE dbo.T SET a = 1; COMMIT; END TRY "
                              "BEGIN CATCH IF XACT_STATE() = -1 ROLLBACK; END CATCH"))
        self.assertIn("CASE WHEN v_in_transaction THEN -1 ELSE 0 END) = -1", sql)

    def test_nested_transactions(self):
        sql, warns = convert(proc("BEGIN TRAN; BEGIN TRAN; UPDATE dbo.T SET a = 1; COMMIT; COMMIT;"))
        f = flat(sql)
        self.assertEqual(f.count("BEGIN TRANSACTION;"), 1)
        self.assertEqual(f.count("COMMIT TRANSACTION;"), 1)
        self.assertTrue(has(warns, "nested BEGIN TRANSACTION removed"))

    def test_begin_tran_in_both_branches_is_not_nested(self):
        f = flat(convert(proc("IF @f = 1 BEGIN TRAN; ELSE BEGIN TRAN; UPDATE dbo.T SET a = 1; COMMIT;",
                              "@f BIT"))[0])
        self.assertEqual(f.count("BEGIN TRANSACTION;"), 2)

    def test_user_functions_use_session_user(self):
        f = flat(convert(proc("SELECT SUSER_SNAME() AS a, SYSTEM_USER AS b, USER_NAME() AS c;"))[0])
        self.assertEqual(f.count("SESSION_USER()"), 3)
        self.assertNotIn("CURRENT_USER", f)

    def test_parsename(self):
        f = flat(convert(proc("SELECT PARSENAME('a.b.c', 1) AS p, PARSENAME(Name, 2) AS q FROM dbo.T;"))[0])
        self.assertIn("'c' AS p", f)
        self.assertIn("ARRAY_REVERSE(SPLIT(Name, '.'))[SAFE_OFFSET(2 - 1)] AS q", f)
        self.assertNotIn("SPLIT_PART", f)

    def test_at_time_zone_on_datetime(self):
        f = flat(convert(proc("SELECT CreatedAt AT TIME ZONE 'UTC' AS t FROM dbo.T;"))[0])
        self.assertIn("TIMESTAMP(CreatedAt, 'UTC') AS t", f)
        self.assertNotIn("DATETIME(CreatedAt", f)

    def test_windows_time_zone_mapped(self):
        sql, warns = convert(proc("SELECT x AT TIME ZONE 'UTC' AT TIME ZONE 'India Standard Time' AS t FROM dbo.T;"))
        self.assertIn("DATETIME(TIMESTAMP(x, 'UTC'), 'Asia/Kolkata')", flat(sql))
        self.assertTrue(has(warns, "mapped to IANA name 'Asia/Kolkata'"))

    def test_unpivot_columns_not_quoted(self):
        f = flat(convert(proc("SELECT * FROM dbo.T UNPIVOT (Val FOR Col IN (A, B)) AS u;"))[0])
        self.assertIn("UNPIVOT(Val FOR Col IN (A, B))", f)

    def test_tvp_is_not_read_as_a_table(self):
        sql, warns = convert("CREATE PROC dbo.P @items dbo.ItemList READONLY AS SELECT COUNT(*) FROM @items")
        self.assertNotIn("`proj.ds.p_items`", sql)
        self.assertIn("FROM UNNEST(p_items)", sql)
        self.assertTrue(has(warns, "type dbo.ItemList is unknown", "review"))

    def test_rand_seed(self):
        sql, warns = convert(proc("SELECT RAND(42) AS r;"))
        self.assertIn("RAND() AS r", flat(sql))
        self.assertTrue(has(warns, "takes no seed", "review"))

    def test_cast_decimal_to_int_truncates(self):
        f = flat(convert(proc("SELECT CAST(2.7 AS INT) AS a, CAST(1 AS INT) AS b;"))[0])
        self.assertIn("CAST(TRUNC(2.7) AS INT64) AS a", f)
        self.assertIn("CAST(1 AS INT64) AS b", f)

    def test_object_id_in_if_condition(self):
        sql, warns = convert(proc("IF OBJECT_ID('dbo.T') IS NOT NULL SELECT 1;"))
        self.assertIn("IF EXISTS(SELECT 1 FROM `proj.ds.INFORMATION_SCHEMA.TABLES` WHERE table_name = 'T') THEN", sql)
        self.assertFalse(has(warns, "OBJECT_ID() has no equivalent"))

    def test_watchlist_runs_on_conditions(self):
        _, warns = convert(proc("IF @@SPID > 0 SELECT 1; WHILE HOST_NAME() = 'x' BREAK;"))
        self.assertTrue(has(warns, "HOST_NAME: no equivalent", "review"))


class NewConversionTests(unittest.TestCase):
    """v4 flagged (or left) these; BQMS or a known BigQuery idiom converts them."""

    def test_string_split_from_and_apply(self):
        f = flat(convert(proc("SELECT value FROM STRING_SPLIT(@s, ','); "
                              "SELECT t.Id, x.value FROM dbo.T t CROSS APPLY STRING_SPLIT(t.Tags, ';') x;",
                              "@s VARCHAR(100)"))[0])
        self.assertIn("FROM UNNEST(SPLIT(p_s, ',')) AS value", f)
        self.assertIn("CROSS JOIN UNNEST(ARRAY(SELECT AS STRUCT value FROM UNNEST(SPLIT(t.Tags, ';')) AS value)) AS x", f)
        self.assertNotIn("STRING_SPLIT", f)

    def test_cross_and_outer_apply(self):
        f = flat(convert(proc("SELECT c.Id, x.T FROM dbo.C c CROSS APPLY (SELECT SUM(a) AS T FROM dbo.O o "
                              "WHERE o.Id = c.Id) x; SELECT c.Id, y.T FROM dbo.C c OUTER APPLY (SELECT TOP 1 a AS T "
                              "FROM dbo.O o WHERE o.Id = c.Id ORDER BY o.d DESC) y;"))[0])
        self.assertIn("CROSS JOIN UNNEST(ARRAY(SELECT AS STRUCT SUM(a) AS T FROM `proj.ds.O` AS o WHERE o.Id = c.Id)) AS x", f)
        self.assertIn("LEFT JOIN UNNEST(ARRAY(SELECT AS STRUCT a AS T FROM `proj.ds.O` AS o WHERE o.Id = c.Id "
                      "ORDER BY o.d DESC LIMIT 1)) AS y ON TRUE", f)
        self.assertNotIn("LATERAL", f)

    def test_aggregates(self):
        f = flat(convert(proc("SELECT VAR(a), VARP(a), STDEVP(a) FROM dbo.T;"))[0])
        for frag in ("VAR_SAMP(a)", "VAR_POP(a)", "STDDEV_POP(a)"):
            self.assertIn(frag, f)

    def test_percentile_cont_window(self):
        f = flat(convert(proc("SELECT PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY a) OVER (PARTITION BY g) AS m "
                              "FROM dbo.T;"))[0])
        self.assertIn("PERCENTILE_CONT(a, 0.5) OVER (PARTITION BY g) AS m", f)

    def test_grouping_id(self):
        f = flat(convert(proc("SELECT GROUPING_ID(a, b) AS g FROM dbo.T GROUP BY ROLLUP(a, b);"))[0])
        self.assertIn("GROUPING(a) * 2 + GROUPING(b)", f)

    def test_recursive_cte(self):
        f = flat(convert(proc("WITH n AS (SELECT 1 AS i UNION ALL SELECT i + 1 FROM n WHERE i < 5) SELECT i FROM n;"))[0])
        self.assertIn("WITH RECURSIVE n AS", f)

    def test_top_with_ties_and_percent(self):
        f = flat(convert(proc("SELECT TOP 3 WITH TIES Id FROM dbo.T ORDER BY a DESC; "
                              "SELECT TOP 10 PERCENT Id FROM dbo.T ORDER BY Id;"))[0])
        self.assertIn("QUALIFY RANK() OVER (ORDER BY a DESC) <= 3", f)
        self.assertIn("QUALIFY ROW_NUMBER() OVER (ORDER BY Id) <= CEIL(COUNT(*) OVER () * 10 / 100.0)", f)
        self.assertNotIn("WITH TIES", f)
        self.assertNotIn("PERCENT", f)

    def test_for_json_path(self):
        f = flat(convert(proc("SELECT Id, Name FROM dbo.T ORDER BY Id FOR JSON PATH;"))[0])
        self.assertIn("NULLIF(TO_JSON_STRING(ARRAY(SELECT AS STRUCT Id, Name FROM `proj.ds.T` ORDER BY Id)), '[]') AS json", f)

    def test_for_json_dotted_alias_still_flagged(self):
        _, warns = convert(proc("SELECT Id AS [a.b] FROM dbo.T FOR JSON PATH;"))
        self.assertTrue(has(warns, "dotted aliases", "review"))

    def test_at_error_if(self):
        f = flat(convert(proc("UPDATE dbo.T SET a = 1; IF @@ERROR <> 0 SELECT 1 AS failed;"))[0])
        self.assertIn("BEGIN UPDATE `proj.ds.T` SET a = 1 WHERE TRUE; EXCEPTION WHEN ERROR THEN SELECT 1 AS failed; END;", f)
        self.assertNotIn("@@ERROR", f)

    def test_set_var_at_error(self):
        f = flat(convert(proc("DECLARE @e INT; DELETE FROM dbo.T WHERE a = 1; SET @e = @@ERROR;"))[0])
        self.assertIn("SET v_e = 0; EXCEPTION WHEN ERROR THEN SET v_e = 1; END;", f)

    def test_error_line(self):
        f = flat(convert(proc("BEGIN TRY SELECT 1; END TRY BEGIN CATCH SELECT ERROR_LINE() AS l; END CATCH"))[0])
        self.assertIn("@@error.stack_trace[SAFE_OFFSET(0)].line AS l", f)

    def test_scalar_functions(self):
        f = flat(convert(proc("SELECT NCHAR(66) a, SPACE(2) b, PATINDEX('a%', s) c, CHOOSE(2, 'x', 'y') d, "
                              "DB_NAME() e, FORMATMESSAGE('n=%i', 5) f FROM dbo.T;"))[0])
        for frag in ("CODE_POINTS_TO_STRING([66]) AS a", "REPEAT(' ', 2) AS b", "REGEXP_INSTR(s, '(?s)^a') AS c",
                     "CASE 2 WHEN 1 THEN 'x' WHEN 2 THEN 'y' END AS d", "'ds' AS e", "FORMAT('n=%d', 5) AS f"):
            self.assertIn(frag, f)

    def test_isdate_isnumeric_try_parse_flagged_but_converted(self):
        sql, warns = convert(proc("SELECT ISDATE(s) a, ISNUMERIC(s) b, TRY_PARSE(s AS DATE) c FROM dbo.T;"))
        f = flat(sql)
        self.assertIn("SAFE_CAST(s AS FLOAT64)", f)
        self.assertIn("SAFE_CAST(s AS DATE) AS c", f)
        self.assertNotIn("ISDATE(", f)
        self.assertTrue(has(warns, "only ISO strings", "review"))

    def test_geography(self):
        f = flat(convert(proc("DECLARE @g GEOGRAPHY = geography::Point(12.9, 77.6, 4326); "
                              "SELECT @g.STAsText() AS t, @g.Lat AS la, @g.STDistance(@g) AS d;"))[0])
        self.assertIn("SET v_g = ST_GEOGPOINT(77.6, 12.9);", f)            # Point(lat, long) -> ST_GEOGPOINT(long, lat)
        self.assertIn("ST_ASTEXT(v_g) AS t", f)
        self.assertIn("ST_Y(v_g) AS la", f)
        self.assertIn("ST_DISTANCE(v_g, v_g) AS d", f)

    def test_collate_cs_has_no_case_warning(self):
        _, warns = convert(proc("SELECT Id FROM dbo.T WHERE Name = 'a' COLLATE Latin1_General_CS_AS;"))
        self.assertFalse(has(warns, "case-insensitive"))

    def test_sp_rename_column(self):
        sql, _ = convert(proc("EXEC sp_rename 'dbo.T.Name', 'Name2', 'COLUMN';"))
        self.assertIn("ALTER TABLE `proj.ds.T` RENAME COLUMN Name TO Name2;", sql)

    def test_dynamic_sql_injection_warning(self):
        _, warns = convert("CREATE PROC dbo.P @t SYSNAME AS BEGIN DECLARE @s NVARCHAR(MAX) = N'SELECT * FROM ' + @t; "
                           "EXEC (@s); END")
        self.assertTrue(has(warns, "SQL injection", "review"))

    def test_return_status_out_param(self):
        src = ("CREATE PROC dbo.Callee @a INT AS BEGIN IF @a < 0 RETURN 1; RETURN 0; END\nGO\n"
               "CREATE PROC dbo.Caller AS BEGIN DECLARE @rc INT; EXEC @rc = dbo.Callee @a = 1; SELECT @rc; END")
        callee = [p for p in (parse_batch(b) for b in split_batches(src)) if p]
        reg = {f"dbo.{p.name.lower()}": p for p in callee}
        tr = Translator(Config(project="proj", default_dataset="ds", return_status_mode="out_param"))
        c_sql, _, _ = tr.convert(callee[0], "ds", reg)
        self.assertIn("OUT p_return_status INT64", c_sql)
        self.assertIn("SET p_return_status = 1;", c_sql)
        k_sql, _, _ = tr.convert(callee[1], "ds", reg)
        self.assertIn("CALL `proj.ds.Callee`(1, v_rc);", k_sql)


class FolderLevelTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_table_type_from_create_type(self):
        self.assertEqual(parse_table_type("CREATE TYPE dbo.L AS TABLE (Id INT NOT NULL, N NVARCHAR(5), PRIMARY KEY (Id))"),
                         ("dbo.L", "Id INT64, N STRING"))
        inp = self.tmp / "in"
        inp.mkdir()
        (inp / "t.sql").write_text("CREATE TYPE dbo.L AS TABLE (Id INT, N NVARCHAR(5))\nGO\n", encoding="utf-8")
        (inp / "p.sql").write_text("CREATE PROC dbo.P @l dbo.L READONLY AS SELECT Id FROM @l", encoding="utf-8")
        cfg = Config(project="proj", default_dataset="ds")
        convert_path(inp, self.tmp / "out", cfg)
        out = (self.tmp / "out" / "P.sql").read_text(encoding="utf-8")
        self.assertIn("IN p_l ARRAY<STRUCT<Id INT64, N STRING>>", out)
        self.assertIn("FROM UNNEST(p_l)", out)

    def test_config_typos_and_bad_values(self):
        p = self.tmp / "config.json"
        p.write_text(json.dumps({"project": "my-proj", "bit_column": ["IsActive"]}), encoding="utf-8")
        load_config(p)
        self.assertTrue(any("bit_column" in w for w in CONFIG_WARNINGS))
        p.write_text(json.dumps({"schema_mode": "prefixx"}), encoding="utf-8")
        with self.assertRaises(ValueError):
            load_config(p)

    def test_cp1252_file_is_flagged(self):
        inp = self.tmp / "in"
        inp.mkdir()
        (inp / "p.sql").write_bytes("CREATE PROC dbo.P AS SELECT 'caf\xe9' AS a".encode("cp1252"))
        res = convert_path(inp, self.tmp / "out", Config(project="proj", default_dataset="ds"))
        self.assertTrue(any("not UTF-8" in w["message"] for r in res for w in r.warnings))


if __name__ == "__main__":
    unittest.main()


class CommentSafetyTests(unittest.TestCase):
    def test_parse_error_message_stays_inside_comment(self):
        sql, _ = convert("CREATE PROC dbo.P AS BEGIN CREATE TABLE #r (a INT); INSERT INTO #r EXEC dbo.usp_Other; END")
        self.assertNotIn("\x1b", sql)
        code = [l for l in sql.splitlines() if l.strip() and not l.lstrip().startswith("--")]
        self.assertFalse(any("EXEC" in l for l in code), code)     # the failed statement only appears in comments
