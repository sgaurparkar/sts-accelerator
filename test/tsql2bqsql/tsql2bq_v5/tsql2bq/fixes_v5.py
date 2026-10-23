"""v5 expression rewrites (T-SQL constructs that v4 left as-is, flagged, or converted wrongly).

`early()` runs at the start of Translator.postprocess, so the nodes it creates still go through the later fixes
(table qualification, BIT handling, ...).  `late()` runs at the end, for rewrites that must not be touched again
(INFORMATION_SCHEMA references).
"""
import re
from typing import List, Optional

import sqlglot
from sqlglot import exp

# Windows time zone names (SQL Server AT TIME ZONE) -> IANA names (BigQuery), CLDR "001" mapping
WINDOWS_TZ = {
    "utc": "UTC", "coordinated universal time": "UTC", "gmt standard time": "Europe/London",
    "greenwich standard time": "Atlantic/Reykjavik", "india standard time": "Asia/Kolkata",
    "pacific standard time": "America/Los_Angeles", "mountain standard time": "America/Denver",
    "us mountain standard time": "America/Phoenix", "central standard time": "America/Chicago",
    "eastern standard time": "America/New_York", "atlantic standard time": "America/Halifax",
    "alaskan standard time": "America/Anchorage", "hawaiian standard time": "Pacific/Honolulu",
    "w. europe standard time": "Europe/Berlin", "central europe standard time": "Europe/Budapest",
    "central european standard time": "Europe/Warsaw", "romance standard time": "Europe/Paris",
    "e. europe standard time": "Europe/Chisinau", "fle standard time": "Europe/Kiev",
    "gtb standard time": "Europe/Bucharest", "russian standard time": "Europe/Moscow",
    "turkey standard time": "Europe/Istanbul", "israel standard time": "Asia/Jerusalem",
    "egypt standard time": "Africa/Cairo", "south africa standard time": "Africa/Johannesburg",
    "arab standard time": "Asia/Riyadh", "arabian standard time": "Asia/Dubai",
    "pakistan standard time": "Asia/Karachi", "sri lanka standard time": "Asia/Colombo",
    "nepal standard time": "Asia/Kathmandu", "bangladesh standard time": "Asia/Dhaka",
    "se asia standard time": "Asia/Bangkok", "singapore standard time": "Asia/Singapore",
    "china standard time": "Asia/Shanghai", "taipei standard time": "Asia/Taipei",
    "tokyo standard time": "Asia/Tokyo", "korea standard time": "Asia/Seoul",
    "w. australia standard time": "Australia/Perth", "aus eastern standard time": "Australia/Sydney",
    "new zealand standard time": "Pacific/Auckland", "e. south america standard time": "America/Sao_Paulo",
    "argentina standard time": "America/Buenos_Aires", "sa pacific standard time": "America/Bogota",
    "central america standard time": "America/Guatemala", "mexico standard time": "America/Mexico_City",
    "central standard time (mexico)": "America/Mexico_City",
}

# geography / geometry instance methods -> BigQuery ST_ functions (receiver becomes the first argument)
GEO_METHODS = {
    "stastext": "ST_ASTEXT", "tostring": "ST_ASTEXT", "stasbinary": "ST_ASBINARY", "stdistance": "ST_DISTANCE",
    "stintersects": "ST_INTERSECTS", "stcontains": "ST_CONTAINS", "stwithin": "ST_WITHIN",
    "stequals": "ST_EQUALS", "sttouches": "ST_TOUCHES", "stdisjoint": "ST_DISJOINT", "starea": "ST_AREA",
    "stlength": "ST_LENGTH", "stbuffer": "ST_BUFFER", "stunion": "ST_UNION", "stintersection": "ST_INTERSECTION",
    "stdifference": "ST_DIFFERENCE", "stcentroid": "ST_CENTROID", "stnumpoints": "ST_NUMPOINTS",
    "stisempty": "ST_ISEMPTY", "stx": "ST_X", "sty": "ST_Y", "stdimension": "ST_DIMENSION",
    "stboundary": "ST_BOUNDARY", "stconvexhull": "ST_CONVEXHULL", "stpointn": "ST_POINTN",
    "ststartpoint": "ST_STARTPOINT", "stendpoint": "ST_ENDPOINT",
}
GEO_FACTORIES = {"stgeomfromtext", "stgeogfromtext", "stpointfromtext", "stlinefromtext", "stpolyfromtext",
                 "stmpointfromtext", "stmlinefromtext", "stmpolyfromtext", "parse", "stgeomcollfromtext"}

_PH = re.compile(r"^__a(\d+)__$")


def tmpl(sql: str, *args) -> exp.Expression:
    """Parse a BigQuery fragment and put copies of *args* where __a0__, __a1__ ... appear."""
    tree = sqlglot.parse_one(sql, read="bigquery")
    for col in list(tree.find_all(exp.Column)):
        m = _PH.match(col.name or "")
        if m and not col.table:
            col.replace(args[int(m.group(1))].copy())
    if isinstance(tree, exp.Column):          # whole template is one placeholder
        m = _PH.match(tree.name or "")
        if m:
            return args[int(m.group(1))].copy()
    return tree


def _lit_str(n) -> Optional[str]:
    return str(n.this) if isinstance(n, exp.Literal) and n.is_string else None


def _lit_int(n) -> Optional[int]:
    if isinstance(n, exp.Neg) and isinstance(n.this, exp.Literal) and not n.this.is_string:
        v = _lit_int(n.this)
        return -v if v is not None else None
    if isinstance(n, exp.Literal) and not n.is_string:
        try:
            return int(float(str(n.this)))
        except ValueError:
            return None
    return None


def _fname(n) -> str:
    return str(n.name).upper() if isinstance(n, exp.Anonymous) else ""


def _array_of_struct(select: exp.Select) -> exp.Expression:
    """ARRAY(SELECT AS STRUCT ...) built around a copy of *select*."""
    inner = select.copy()
    inner.set("kind", "STRUCT")
    arr = sqlglot.parse_one("ARRAY(SELECT 1)", read="bigquery")
    arr.find(exp.Select).replace(inner)
    return arr


# -----------------------------------------------------------------------------------------------------------
def early(tr, tree, ctx):
    _recursive_cte(tree, ctx)
    _apply(tr, tree, ctx)
    _string_split(tree, ctx)
    _parsename(tree, ctx)
    _aggregates(tree, ctx)
    _scalar_functions(tr, tree, ctx)
    _geography(tree, ctx)
    _at_time_zone(tr, tree, ctx)
    _collate_cs(tree)


def late(tr, tree, ctx):
    _object_id(tr, tree, ctx)


# -- WITH ... (self reference) -> WITH RECURSIVE ------------------------------------------------------------
def _recursive_cte(tree, ctx):
    for w in tree.find_all(exp.With):
        if w.args.get("recursive"):
            continue
        for cte in w.expressions:
            name = (cte.alias or "").lower()
            if name and any(t.name.lower() == name and not t.db for t in cte.this.find_all(exp.Table)):
                w.set("recursive", True)
                ctx.warn("info", f"recursive CTE {cte.alias}: WITH RECURSIVE added (BigQuery stops after 500 "
                                 f"iterations; T-SQL's default MAXRECURSION is 100, OPTION (MAXRECURSION 0) = no limit)")
                break


# -- CROSS / OUTER APPLY -> [CROSS|LEFT] JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) ----------------------------
def _apply(tr, tree, ctx):
    for lat in list(tree.find_all(exp.Lateral)):
        join = lat.parent
        if not isinstance(join, exp.Join):
            continue
        outer = str(join.args.get("side") or "").upper() == "LEFT" or lat.args.get("cross_apply") is False
        alias = lat.args.get("alias")
        alias_name = alias.name if alias is not None else None
        body = lat.this
        sel = None
        if isinstance(body, exp.Subquery) and isinstance(body.this, exp.Select):
            sel = body.this
            if any(isinstance(e, exp.Star) for e in sel.expressions) and len(sel.expressions) > 1:
                sel = None
        elif _fname(body) == "STRING_SPLIT" and len(body.expressions) >= 2:
            sel = _split_select(body.expressions)
        if sel is None or not alias_name:
            ctx.warn("review", "CROSS/OUTER APPLY with a table-valued function or unnamed columns is not converted: "
                               "rewrite as JOIN UNNEST(ARRAY(SELECT AS STRUCT ...)) or a join", join.sql("tsql"))
            continue
        # BigQuery UNNEST aliases are column aliases: TableAlias(columns=[x]) renders "UNNEST(...) AS x"
        unnest = exp.Unnest(expressions=[_array_of_struct(sel)],
                            alias=exp.TableAlias(columns=[exp.to_identifier(alias_name)]))
        if outer:
            new = exp.Join(this=unnest, side="LEFT", on=exp.true())
        else:
            new = exp.Join(this=unnest, kind="CROSS")
        join.replace(new)
        ctx.warn("info", f"{'OUTER' if outer else 'CROSS'} APPLY -> {'LEFT JOIN' if outer else 'CROSS JOIN'} "
                         f"UNNEST(ARRAY(SELECT AS STRUCT ...)) AS {alias_name} (correlated; dry-run it - BigQuery "
                         f"rejects correlated subqueries it cannot de-correlate)")


def _split_select(args) -> exp.Select:
    ordinal = len(args) > 2 and _lit_int(args[2]) == 1
    if ordinal:
        return tmpl("SELECT value, off + 1 AS ordinal FROM UNNEST(SPLIT(__a0__, __a1__)) AS value WITH OFFSET AS off",
                    args[0], args[1])
    return tmpl("SELECT value FROM UNNEST(SPLIT(__a0__, __a1__)) AS value", args[0], args[1])


# -- FROM STRING_SPLIT(s, sep) -> FROM (SELECT value FROM UNNEST(SPLIT(s, sep)) AS value) ---------------------
def _string_split(tree, ctx):
    for tbl in list(tree.find_all(exp.Table)):
        fn = tbl.this
        if _fname(fn) != "STRING_SPLIT" or len(fn.expressions) < 2:
            continue
        sub = exp.Subquery(this=_split_select(fn.expressions))
        alias = tbl.args.get("alias")
        sub.set("alias", alias.copy() if alias is not None else exp.TableAlias(this=exp.to_identifier("value_list")))
        tbl.replace(sub)
        ctx.warn("info", "STRING_SPLIT(s, sep) -> UNNEST(SPLIT(s, sep)) AS value (same rows; the order of the "
                         "values is not guaranteed in either engine unless enable_ordinal / WITH OFFSET is used)")


# -- PARSENAME ------------------------------------------------------------------------------------------------
def _parsename(tree, ctx):
    for sp in list(tree.find_all(exp.SplitPart)):       # sqlglot pre-computes PARSENAME('lit', n) as SPLIT_PART
        s, idx = _lit_str(sp.this), _lit_int(sp.args.get("part_index"))
        if s is not None and idx is not None:
            parts = s.split(".")
            val = parts[idx - 1].strip("[]") if 1 <= idx <= len(parts) else None
            sp.replace(exp.Literal.string(val) if val is not None else exp.cast(exp.null(), "text"))
    for fn in list(tree.find_all(exp.Anonymous)):
        if _fname(fn) == "PARSENAME" and len(fn.expressions) == 2:
            fn.replace(tmpl("ARRAY_REVERSE(SPLIT(__a0__, '.'))[SAFE_OFFSET(__a1__ - 1)]", *fn.expressions))
            ctx.warn("info", "PARSENAME(s, n) -> ARRAY_REVERSE(SPLIT(s, '.'))[SAFE_OFFSET(n - 1)] ([brackets] are not "
                             "removed and names with more than 4 parts are not NULL as in T-SQL)")


# -- aggregates -----------------------------------------------------------------------------------------------
def _aggregates(tree, ctx):
    names = {"VAR": "VAR_SAMP", "VARP": "VAR_POP", "STDEVP": "STDDEV_POP", "STDEV": "STDDEV_SAMP"}
    for fn in list(tree.find_all(exp.Anonymous)):
        nm = _fname(fn)
        if nm in names:
            fn.replace(exp.Anonymous(this=names[nm], expressions=[e.copy() for e in fn.expressions]))
    # PERCENTILE_CONT/DISC(p) WITHIN GROUP (ORDER BY x) OVER (PARTITION BY ...) -> PERCENTILE_CONT(x, p) OVER (...)
    for wg in list(tree.find_all(exp.WithinGroup)):
        fn, order = wg.this, wg.expression
        if not isinstance(fn, (exp.PercentileCont, exp.PercentileDisc)) or not isinstance(order, exp.Order) \
                or len(order.expressions) != 1:
            continue
        name = "PERCENTILE_CONT" if isinstance(fn, exp.PercentileCont) else "PERCENTILE_DISC"
        win = wg.parent
        if not isinstance(win, exp.Window):
            ctx.warn("review", f"{name} ... WITHIN GROUP used as an aggregate (no OVER): BigQuery only has it as a "
                               f"window function - use APPROX_QUANTILES(x, 100)[OFFSET(50)] or "
                               f"{name}(x, p) OVER (PARTITION BY ...) with SELECT DISTINCT", wg.sql("tsql"))
            continue
        o = order.expressions[0]
        p = fn.this.copy()
        if o.args.get("desc"):
            if name == "PERCENTILE_DISC":
                ctx.warn("review", "PERCENTILE_DISC ... ORDER BY x DESC: rewrite with 1 - p and check the boundary row",
                         wg.sql("tsql"))
                continue
            p = exp.Sub(this=exp.Literal.number(1), expression=exp.paren(p))
        new_fn = exp.Anonymous(this=name, expressions=[o.this.copy(), p])
        new_win = win.copy()
        new_win.set("this", new_fn)
        new_win.set("order", None)
        win.replace(new_win)
    # GROUPING_ID(a, b, c) = GROUPING(a) * 4 + GROUPING(b) * 2 + GROUPING(c)
    for g in list(tree.find_all(exp.GroupingId)):
        cols = list(g.expressions)
        if not cols:
            continue
        terms = []
        for i, c in enumerate(cols):
            w = 2 ** (len(cols) - 1 - i)
            term = exp.Anonymous(this="GROUPING", expressions=[c.copy()])
            terms.append(term if w == 1 else exp.Mul(this=term, expression=exp.Literal.number(w)))
        expr = terms[0]
        for t in terms[1:]:
            expr = exp.Add(this=expr, expression=t)
        g.replace(exp.paren(expr) if len(terms) > 1 else expr)
    for r in list(tree.find_all(exp.Rand)):
        if r.this is not None:
            r.replace(exp.Rand())
            ctx.warn("review", "RAND(seed): BigQuery RAND() takes no seed, so the value is not repeatable", r.sql("tsql"))
        sel = r.find_ancestor(exp.Select)
        if sel is not None and sel.args.get("from_") is not None and "rand_rows" not in ctx.ci_hints:
            ctx.ci_hints.add("rand_rows")
            ctx.warn("info", "RAND() in a query: T-SQL evaluates it ONCE per query (same value on every row), "
                             "BigQuery once per row")


# -- scalar functions ----------------------------------------------------------------------------------------
def _scalar_functions(tr, tree, ctx):
    mode = (tr.cfg.case_sensitivity or "warn").lower()
    for cu in list(tree.find_all(exp.CurrentUser)):
        cu.replace(exp.Anonymous(this="SESSION_USER", expressions=[]))
        _once(ctx, "user", "info", "SUSER_SNAME() / SYSTEM_USER / CURRENT_USER -> SESSION_USER(): returns the Google "
                                   "account e-mail (user or service account), not the SQL Server login")
    for fn in list(tree.find_all(exp.Anonymous)):
        nm, args = _fname(fn), list(fn.expressions)
        if nm in ("USER_NAME", "SUSER_NAME", "ORIGINAL_LOGIN") and len(args) == 0:
            fn.replace(exp.Anonymous(this="SESSION_USER", expressions=[]))
            _once(ctx, "user", "info", f"{nm}() -> SESSION_USER(): returns the Google account e-mail, not the "
                                       f"SQL Server user/login name")
        elif nm == "DB_NAME" and not args:
            fn.replace(exp.Literal.string(ctx.dataset))
            ctx.warn("info", f"DB_NAME() replaced by the target dataset name '{ctx.dataset}'")
        elif nm == "NCHAR" and len(args) == 1:
            fn.replace(tmpl("CODE_POINTS_TO_STRING([__a0__])", args[0]))
        elif nm == "SPACE" and len(args) == 1:
            fn.replace(exp.Anonymous(this="REPEAT", expressions=[exp.Literal.string(" "), args[0].copy()]))
        elif nm == "PATINDEX" and len(args) == 2:
            pat = _lit_str(args[0])
            if pat is None:
                ctx.warn("review", "PATINDEX with a non-constant pattern: rewrite with REGEXP_INSTR", fn.sql("tsql"))
                continue
            rx = _patindex_regex(tr, pat)
            if mode == "lower":
                rx = "(?i)" + rx
            fn.replace(exp.Anonymous(this="REGEXP_INSTR", expressions=[args[1].copy(), exp.Literal.string(rx)]))
            ctx.warn("info", f"PATINDEX('{pat}', x) -> REGEXP_INSTR(x, '{rx}')" +
                     ("" if mode == "lower" else " (case-sensitive; SQL Server's default collation is not - set "
                                                 "case_sensitivity 'lower' to add (?i))"))
        elif nm in ("TRY_PARSE", "PARSE") and len(args) == 1 and isinstance(args[0], exp.Alias):
            typ = args[0].alias
            try:
                dt = exp.DataType.build(typ, dialect="tsql")
            except Exception:  # noqa: BLE001
                continue
            cls = exp.TryCast if nm == "TRY_PARSE" else exp.Cast
            fn.replace(cls(this=args[0].this.copy(), to=dt))
            ctx.warn("review", f"{nm}(x AS {typ}) -> {'SAFE_CAST' if nm == 'TRY_PARSE' else 'CAST'}: BigQuery only "
                               f"parses ISO formats (YYYY-MM-DD, plain numbers); T-SQL also accepts culture formats "
                               f"('12/31/2020', '$1,000') - use SAFE.PARSE_DATE('%m/%d/%Y', x) for those", fn.sql("tsql"))
        elif nm == "ISDATE" and len(args) == 1:
            fn.replace(tmpl("IF(SAFE_CAST(__a0__ AS DATETIME) IS NOT NULL OR SAFE_CAST(__a0__ AS DATE) IS NOT NULL, 1, 0)",
                            args[0]))
            ctx.warn("review", "ISDATE(x) -> IF(SAFE_CAST(x AS DATETIME/DATE) IS NOT NULL, 1, 0): only ISO strings "
                               "count as dates in BigQuery ('Jan 1 2020', '01/02/2020' return 0 here, 1 in T-SQL)")
        elif nm == "ISNUMERIC" and len(args) == 1:
            fn.replace(tmpl("IF(SAFE_CAST(__a0__ AS FLOAT64) IS NOT NULL, 1, 0)", args[0]))
            ctx.warn("review", "ISNUMERIC(x) -> IF(SAFE_CAST(x AS FLOAT64) IS NOT NULL, 1, 0): T-SQL also returns 1 "
                               "for '$', ',', '.', '-', '1e5d' ... and 0 for 'NaN'/'inf'; check the inputs you expect")
        elif nm == "CHOOSE" and len(args) >= 2:
            fn.replace(exp.Case(this=args[0].copy(),
                                ifs=[exp.If(this=exp.Literal.number(i), true=a.copy()) for i, a in enumerate(args[1:], 1)]))
        elif nm == "FORMATMESSAGE" and args and _lit_str(args[0]) is not None:
            f = re.sub(r"%([-+ 0#]*\d*)[iu]", r"%\1d", _lit_str(args[0]))
            fn.replace(exp.Anonymous(this="FORMAT", expressions=[exp.Literal.string(f)] + [a.copy() for a in args[1:]]))
            ctx.warn("info", "FORMATMESSAGE -> FORMAT (printf-style; %i/%u became %d)")
        elif nm == "DIFFERENCE" and len(args) == 2:
            fn.replace(tmpl("(SELECT COUNTIF(SUBSTR(SOUNDEX(__a0__), i, 1) = SUBSTR(SOUNDEX(__a1__), i, 1)) "
                            "FROM UNNEST([1, 2, 3, 4]) AS i)", *args))
            ctx.warn("review", "DIFFERENCE(a, b) -> position-by-position SOUNDEX comparison (0-4); T-SQL's algorithm "
                               "can score differently for some inputs")
    for sp in list(tree.find_all(exp.StrPosition)):          # CHARINDEX(sub, s) -> INSTR(s, sub)
        sub = sp.args.get("substr")
        txt = _lit_str(sub)
        if txt is None or not re.search(r"[A-Za-z]", txt):
            continue
        if mode == "lower":
            sp.set("this", exp.Lower(this=sp.this.copy()))
            sp.set("substr", exp.Lower(this=sub.copy()))
        else:
            _once(ctx, "charindex", "info", "CHARINDEX/INSTR: SQL Server's default collation finds 'A' in 'abc', "
                                            "BigQuery INSTR is case-sensitive (case_sensitivity 'lower' wraps both "
                                            "sides in LOWER())")
    for node in list(tree.find_all(exp.Contains)):          # full-text CONTAINS(col, 'term')
        txt = _lit_str(node.expression)
        if txt is not None and re.search(r'["*]|\b(AND|OR|NOT|NEAR|FORMSOF|ISABOUT)\b', txt, re.I):
            ctx.warn("review", f"full-text CONTAINS(col, '{txt}') uses search operators: CONTAINS_SUBSTR is a plain "
                               f"case-insensitive substring test - use SEARCH() with a search index or REGEXP_CONTAINS")
        else:
            _once(ctx, "contains", "info", "full-text CONTAINS -> CONTAINS_SUBSTR (case-insensitive SUBSTRING match, "
                                           "not word match: 'cat' also matches 'concatenate'); consider SEARCH()")


def _once(ctx, key, level, msg):
    k = "__once_" + key
    if k not in ctx.ci_hints:
        ctx.ci_hints.add(k)
        ctx.warn(level, msg)


def _patindex_regex(tr, pat: str) -> str:
    lead, trail = pat.startswith("%"), pat.endswith("%") and len(pat) > 1
    core = pat[1 if lead else 0: len(pat) - 1 if trail else len(pat)]
    rx = tr.like_to_regex(core, None)            # "(?s)^...$"
    body = rx[len("(?s)^"):-1]
    return "(?s)" + ("" if lead else "^") + body + ("" if trail else "$")


# -- geography ------------------------------------------------------------------------------------------------
def _geography(tree, ctx):
    for sr in list(tree.find_all(exp.ScopeResolution)):
        typ = (sr.this.name if sr.this is not None else "").upper()
        fn = sr.expression
        if typ not in ("GEOGRAPHY", "GEOMETRY") or not isinstance(fn, exp.Anonymous):
            continue
        nm, args = str(fn.name).lower(), list(fn.expressions)
        srid = _lit_int(args[-1]) if len(args) >= 2 else None
        if nm == "point" and len(args) == 3:
            # geography::Point(lat, long, srid) ; geometry::Point(x, y, srid)
            a, b = (args[1], args[0]) if typ == "GEOGRAPHY" else (args[0], args[1])
            sr.replace(exp.Anonymous(this="ST_GEOGPOINT", expressions=[a.copy(), b.copy()]))
        elif nm in GEO_FACTORIES and args:
            sr.replace(exp.Anonymous(this="ST_GEOGFROMTEXT", expressions=[args[0].copy()]))
        else:
            ctx.warn("review", f"{typ}::{fn.name}() has no direct BigQuery equivalent", sr.sql("tsql"))
            continue
        if typ == "GEOMETRY":
            _once(ctx, "geometry", "review", "GEOMETRY (planar) mapped to BigQuery GEOGRAPHY (spherical, WGS84): "
                                             "distances/areas are in metres on the sphere, not in the planar units")
        if srid not in (None, 4326):
            ctx.warn("review", f"SRID {srid}: BigQuery GEOGRAPHY only supports WGS84 (4326) - reproject the data")
    for d in list(tree.find_all(exp.Dot)):
        fn = d.expression
        if not isinstance(fn, exp.Anonymous) or str(fn.name).lower() not in GEO_METHODS:
            continue
        recv = d.this
        if isinstance(recv, exp.Identifier):
            recv = exp.column(recv.copy())
        d.replace(exp.Anonymous(this=GEO_METHODS[str(fn.name).lower()],
                                expressions=[recv.copy()] + [e.copy() for e in fn.expressions]))
    geo_vars = {bq.lower() for bq, typ in ctx.vars.values() if typ == "GEOGRAPHY"}
    for col in list(tree.find_all(exp.Column)):
        if col.table and col.table.lower() in geo_vars and col.name.lower() in ("lat", "long", "stx", "sty"):
            f = "ST_Y" if col.name.lower() in ("lat", "sty") else "ST_X"
            col.replace(exp.Anonymous(this=f, expressions=[exp.column(col.table)]))


# -- AT TIME ZONE ---------------------------------------------------------------------------------------------
def _at_time_zone(tr, tree, ctx):
    for n in reversed(list(tree.find_all(exp.AtTimeZone))):          # innermost first
        if n.parent is None and n is not tree:
            continue
        z = n.args.get("zone")
        zs = _lit_str(z)
        if zs is None:
            ctx.warn("review", "AT TIME ZONE with a non-constant zone: map Windows zone names to IANA names",
                     n.sql("tsql"))
            continue
        iana = WINDOWS_TZ.get(zs.strip().lower())
        if iana is None:
            if " " in zs:
                ctx.warn("review", f"AT TIME ZONE '{zs}': Windows time zone name with no known IANA mapping")
            iana = zs
        elif iana != zs:
            ctx.warn("info", f"time zone '{zs}' mapped to IANA name '{iana}'")
        inner = n.this
        inner_ts = isinstance(inner, exp.Anonymous) and str(inner.name).upper() == "TIMESTAMP" \
            or tr.type_of(inner, ctx) == "timestamp" or (inner.meta.get("tsql2bq_ts") if hasattr(inner, "meta") else False)
        if inner_ts:
            # datetimeoffset AT TIME ZONE z: same instant shown in z. BigQuery TIMESTAMP has no offset -> local DATETIME
            new = exp.Anonymous(this="DATETIME", expressions=[inner.copy(), exp.Literal.string(iana)])
            ctx.warn("review", f"datetimeoffset AT TIME ZONE '{zs}': BigQuery has no offset type - converted to the "
                               f"local DATETIME in {iana}; keep the TIMESTAMP instead if the value is compared with "
                               f"other instants")
        else:
            new = exp.Anonymous(this="TIMESTAMP", expressions=[inner.copy(), exp.Literal.string(iana)])
            new.meta["tsql2bq_ts"] = True
            if tr.type_of(inner, ctx) is None:
                _once(ctx, "atz", "info", "x AT TIME ZONE z -> TIMESTAMP(x, z), assuming x is a DATETIME "
                                          "(wall-clock time in z)")
        n.replace(new)


def _collate_cs(tree):
    """`col = 'x' COLLATE ..._CS_..`: the comparison is case-sensitive on purpose - no case warning for it."""
    for c in tree.find_all(exp.Collate):
        name = c.expression.name.upper() if c.expression is not None else ""
        if "_CS" in name or name.endswith(("_BIN", "_BIN2")):
            p = c.parent
            while p is not None and not isinstance(p, (exp.EQ, exp.NEQ, exp.Like, exp.In)):
                p = p.parent
            if p is not None:
                p.meta["tsql2bq_cs"] = True


# -- OBJECT_ID('x') IS [NOT] NULL -> [NOT] EXISTS (INFORMATION_SCHEMA) ---------------------------------------
def _object_id(tr, tree, ctx):
    from .translator import object_name, routine_name, qualify
    cfg = tr.cfg
    for is_ in list(tree.find_all(exp.Is)):
        fn = is_.this
        if not isinstance(is_.expression, exp.Null):
            continue
        if isinstance(fn, exp.ObjectId):
            args = [a for a in (fn.this, fn.args.get("expression")) if a is not None]
        elif _fname(fn) == "OBJECT_ID":
            args = list(fn.expressions)
        else:
            continue
        name = _lit_str(args[0]) if args else None
        if name is None:
            continue
        kind = (_lit_str(args[1]) or "").upper().strip() if len(args) > 1 else ""
        parts = [p.strip("[]\"") for p in name.split(".")]
        if any(p.startswith("#") for p in parts) or (parts and parts[0].lower() == "tempdb"):
            ctx.warn("review", f"OBJECT_ID('{name}') checks a temp table: BigQuery cannot look temp tables up - use "
                               f"CREATE OR REPLACE TEMP TABLE / DROP TABLE IF EXISTS instead", fn.sql("tsql"))
            continue
        db, schema, obj = qualify([p for p in parts if p != ""] if "" not in parts else [parts[0], "dbo", parts[-1]])
        ds = cfg.dataset_for(db) if db else ctx.dataset
        if kind in ("P", "PC", "FN", "IF", "TF"):
            view, col, target = "ROUTINES", "routine_name", routine_name(cfg, schema, obj)
        else:
            view, col, target = "TABLES", "table_name", object_name(cfg, schema, obj)
        tbl = exp.Table(this=exp.to_identifier(f"{cfg.project}.{ds}.INFORMATION_SCHEMA.{view}", quoted=True))
        sub = exp.select("1").from_(tbl).where(exp.EQ(this=exp.column(col), expression=exp.Literal.string(target)))
        new = exp.Exists(this=sub)
        parent = is_.parent
        if isinstance(parent, exp.Not):                      # NOT OBJECT_ID(...) IS NULL  ==  object exists
            parent.replace(new)
        else:
            is_.replace(exp.Not(this=new))
        ctx.warn("info", f"OBJECT_ID('{name}') IS [NOT] NULL -> [NOT] EXISTS on {ds}.INFORMATION_SCHEMA.{view}")
