"""Translate a parsed T-SQL procedure into a BigQuery stored procedure."""
import logging
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import sqlglot
from sqlglot import exp

from .parser import (Block, If, Param, Parser, ProcDef, Simple, TryCatch, While,
                     _split_commas)
from .tokens import Token, ident_value, tokenize
from . import fixes_v5

logging.getLogger("sqlglot").setLevel(logging.ERROR)

ROWCOUNT_PH = "__bq_row_count__"
ERRMSG_PH = "__bq_error_message__"
ERRLINE_PH = "__bq_error_line__"

# SET options that have no BigQuery equivalent and are safe to drop.
NOOP_SET_OPTIONS = {
    "NOCOUNT", "XACT_ABORT", "ANSI_PADDING",
    "QUOTED_IDENTIFIER", "ARITHABORT", "NUMERIC_ROUNDABORT",
    "TRANSACTION", "LOCK_TIMEOUT",
    "DEADLOCK_PRIORITY",
    "IMPLICIT_TRANSACTIONS", "TEXTSIZE", "STATISTICS", "NOEXEC", "PARSEONLY",
}
# SET options that are removed but change the meaning of the code that follows -> flagged
SEMANTIC_SET_OPTIONS = {
    "DATEFIRST": "DATEPART(weekday) / DATEDIFF(week) depend on it; BigQuery weeks always start on Sunday",
    "DATEFORMAT": "string-to-date conversions depend on it; BigQuery CAST only accepts YYYY-MM-DD",
    "LANGUAGE": "DATENAME() / date parsing depend on it; BigQuery always uses English",
    "ANSI_NULLS": "with OFF, `x = NULL` is TRUE for NULLs; BigQuery always follows ANSI rules",
    "CONCAT_NULL_YIELDS_NULL": "with OFF, 'a' + NULL = 'a'; in BigQuery 'a' || NULL is NULL",
    "ANSI_WARNINGS": "with OFF, divide by zero / overflow return NULL instead of an error",
}

# T-SQL features with no direct BigQuery equivalent -> flagged for review.
WATCHLIST = {
    "SCOPE_IDENTITY": "no identity columns in BigQuery; generate keys explicitly (e.g. GENERATE_UUID())",
    "IDENT_CURRENT": "no identity columns in BigQuery",
    "OBJECT_ID": "OBJECT_ID() has no equivalent; use INFORMATION_SCHEMA or DROP/CREATE IF EXISTS",
    "OBJECT_NAME": "no equivalent",
    "DB_NAME": "no equivalent (use @@project_id / dataset names)",
    "HOST_NAME": "no equivalent", "APP_NAME": "no equivalent",
    "SUSER_SNAME": "use SESSION_USER()", "SYSTEM_USER": "use SESSION_USER()",
    "SUSER_NAME": "use SESSION_USER()",
    "FORMATMESSAGE": "use FORMAT()",
    "OPENJSON": "use JSON functions / UNNEST(JSON_QUERY_ARRAY(...))",
    "OPENXML": "no equivalent", "OPENQUERY": "linked servers not supported",
    "OPENROWSET": "not supported", "PIVOT": "BigQuery PIVOT syntax differs; verify",
    "UNPIVOT": "BigQuery UNPIVOT syntax differs; verify",
    "APPLY": "CROSS/OUTER APPLY -> use CROSS JOIN UNNEST / correlated subquery",
    "HASHBYTES": "use MD5()/SHA256()/SHA512()",
    "CHECKSUM": "use FARM_FINGERPRINT()", "BINARY_CHECKSUM": "use FARM_FINGERPRINT()",
    "PARSENAME": "no equivalent (use SPLIT)", "QUOTENAME": "no equivalent",
    "STUFF": "no equivalent (use SUBSTR/CONCAT)", "ISNUMERIC": "use SAFE_CAST(x AS FLOAT64) IS NOT NULL",
    "ISDATE": "use SAFE_CAST(x AS DATE) IS NOT NULL", "CHOOSE": "use CASE / ARRAY offset",
    "NEWSEQUENTIALID": "use GENERATE_UUID()", "OUTPUT": "OUTPUT clause is not supported",
    "@@IDENTITY": "no identity columns in BigQuery",
    "@@FETCH_STATUS": "cursors are not supported; rewrite with FOR ... IN (SELECT ...)",
    "@@TRANCOUNT": "no equivalent; BigQuery transactions are not nestable",
    "@@ERROR": "use EXCEPTION WHEN ERROR THEN ... @@error.message",
    "@@SPID": "no equivalent", "@@SERVERNAME": "no equivalent", "@@VERSION": "no equivalent",
    "@@NESTLEVEL": "no equivalent",
}

# watchlist entries that v5 converts: only flagged if the name is still in the generated SQL
CONVERTIBLE = {"STUFF", "PARSENAME", "OBJECT_ID", "DB_NAME", "SUSER_SNAME", "SYSTEM_USER", "SUSER_NAME",
               "FORMATMESSAGE", "APPLY", "ISNUMERIC", "ISDATE", "CHOOSE"}
# watchlist entries that are converted correctly but worth a note (not a REVIEW)
WATCHLIST_INFO = {"PIVOT", "UNPIVOT"}

# T-SQL CONVERT(VARCHAR, date, style) -> BigQuery format string
CONVERT_STYLES = {
    100: "%b %e %Y %I:%M%p", 101: "%m/%d/%Y", 102: "%Y.%m.%d", 103: "%d/%m/%Y", 104: "%d.%m.%Y",
    105: "%d-%m-%Y", 108: "%H:%M:%S", 110: "%m-%d-%Y", 111: "%Y/%m/%d", 112: "%Y%m%d",
    120: "%Y-%m-%d %H:%M:%S", 121: "%Y-%m-%d %H:%M:%E3S", 126: "%Y-%m-%dT%H:%M:%E3S",
    127: "%Y-%m-%dT%H:%M:%E3S", 23: "%Y-%m-%d", 24: "%H:%M:%S", 20: "%Y-%m-%d %H:%M:%S",
    21: "%Y-%m-%d %H:%M:%E3S",
}

STRING_CLASSES = tuple(
    getattr(exp, n) for n in (
        "Upper", "Lower", "Trim", "Substring", "Replace", "Concat", "ConcatWs",
        "DPipe", "Left", "Right", "Repeat", "Chr", "Initcap", "Reverse")
    if hasattr(exp, n)
)


# --------------------------------------------------------------------------
# Configuration / context
# --------------------------------------------------------------------------
@dataclass
class Config:
    project: str = "my-project"
    default_dataset: str = "my_dataset"
    dataset_map: Dict[str, str] = field(default_factory=dict)
    param_prefix: str = "p_"
    var_prefix: str = "v_"
    temp_prefix: str = "tmp_"
    schema_mode: str = "prefix"      # prefix: non-dbo schema -> schema_object ; drop: ignore schema
    keep_print: bool = False
    datetime_mode: str = "datetime"  # datetime: GETDATE() -> CURRENT_DATETIME() ; timestamp: -> CURRENT_TIMESTAMP()
    out_param_mode: str = "INOUT"    # how T-SQL OUTPUT parameters are declared: INOUT (faithful) | OUT
    bit_columns: List[str] = field(default_factory=list)   # table columns that are BIT (BOOL in BigQuery)
    column_types: Dict[str, str] = field(default_factory=dict)  # optional column name -> T-SQL type (date, datetime, bit...)
    # SQL Server's default collation is case-insensitive, BigQuery string comparison is case-sensitive:
    #   warn  : flag every string column compared with a text literal (default)
    #   lower : wrap both sides in LOWER() so 'NEW' also matches 'new' (faithful, but defeats clustering/pruning)
    #   off   : do nothing
    case_sensitivity: str = "warn"
    # RETURN <n> status codes: drop (default, keeps signatures unchanged) | out_param (extra OUT return_status INT64
    # parameter; EXEC @rc = proc passes @rc for it)
    return_status_mode: str = "drop"
    # user-defined table types (for table-valued parameters): {"dbo.ItemList": "Id INT64, Name STRING"}. Filled
    # automatically from CREATE TYPE ... AS TABLE batches found in the input folder.
    table_types: Dict[str, str] = field(default_factory=dict)

    def has_dataset(self, key: Optional[str]) -> bool:
        return bool(key) and (key.lower() in {k.lower() for k in self.dataset_map})

    def dataset_for(self, key: Optional[str]) -> str:
        if key:
            low = {k.lower(): v for k, v in self.dataset_map.items()}
            return low.get(key.lower(), self.default_dataset)
        return self.default_dataset


@dataclass
class Warning_:
    level: str      # review | info
    message: str
    snippet: str = ""


@dataclass
class Ctx:
    cfg: Config
    dataset: str
    registry: Dict[str, ProcDef]
    vars: Dict[str, Tuple[str, str]] = field(default_factory=dict)       # lower -> (bq name, bq type)
    table_vars: set = field(default_factory=set)
    temp_names: set = field(default_factory=set)
    string_vars: set = field(default_factory=set)
    params_in: set = field(default_factory=set)
    assigned: set = field(default_factory=set)
    hoisted: Dict[str, str] = field(default_factory=dict)
    warnings: List[Warning_] = field(default_factory=list)
    pending: List[Warning_] = field(default_factory=list)
    loop_depth: int = 0
    catch_depth: int = 0
    out_counter: int = 0
    bit_hints: set = field(default_factory=set)
    ci_hints: set = field(default_factory=set)
    div_hints: set = field(default_factory=set)
    in_tran: bool = False                                 # between BEGIN TRANSACTION and COMMIT/ROLLBACK
    proc_name: str = ""                                   # T-SQL name of the object being converted
    dyn_vars: set = field(default_factory=set)            # variables executed as dynamic SQL (lower, no @)
    dyn_ok: set = field(default_factory=set)              # ... whose value is a converted constant string
    dyn_bad: set = field(default_factory=set)             # ... that are also built / assigned non-constant values
    folder: Optional[str] = None                          # input sub-folder (= SQL Server database) of this object
    try_depth: int = 0                                    # inside BEGIN TRY
    last_dml: bool = False                                # previous emitted statement was INSERT/UPDATE/DELETE/MERGE
    tran_flag: Optional[str] = None                       # BOOL variable that emulates @@TRANCOUNT > 0 / XACT_STATE()
    savepoints: set = field(default_factory=set)
    skip_ids: set = field(default_factory=set)            # statements replaced by a comment (e.g. FETCH before CONTINUE)
    tran_depth: int = 0                                   # T-SQL @@TRANCOUNT as seen statically (nested BEGIN TRAN)
    return_param: Optional[str] = None                    # OUT parameter that carries RETURN <n> (return_status_mode)

    def warn(self, level, message, snippet=""):
        # one line, no terminal colour codes: a multi-line message (sqlglot errors) would otherwise escape the
        # "-- TODO" comment it is written into and become live SQL
        w = Warning_(level, one_line(message), one_line(snippet)[:160])
        self.warnings.append(w)
        self.pending.append(w)


# --------------------------------------------------------------------------
# Types
# --------------------------------------------------------------------------
_TYPE_RE = re.compile(r"^\s*(?:\[?\w+\]?\.)?\[?(\w+)\]?\s*(?:\(\s*([^)]*)\))?\s*$", re.S)
SIMPLE_TYPES = {
    "int": "INT64", "integer": "INT64", "bigint": "INT64", "smallint": "INT64",
    "tinyint": "INT64", "bit": "BOOL", "float": "FLOAT64", "real": "FLOAT64",
    "money": "NUMERIC", "smallmoney": "NUMERIC", "varchar": "STRING",
    "nvarchar": "STRING", "char": "STRING", "nchar": "STRING", "text": "STRING",
    "ntext": "STRING", "sysname": "STRING", "uniqueidentifier": "STRING",
    "xml": "STRING", "date": "DATE", "datetime": "DATETIME",
    "datetime2": "DATETIME", "smalldatetime": "DATETIME",
    "datetimeoffset": "TIMESTAMP", "time": "TIME", "varbinary": "BYTES",
    "binary": "BYTES", "image": "BYTES", "rowversion": "BYTES",
    "timestamp": "BYTES", "geography": "GEOGRAPHY", "geometry": "GEOGRAPHY",
}


def map_type(type_text: str, parameterized: bool = False) -> Tuple[str, Optional[str]]:
    """Return (BigQuery type, note).  note is set when review is needed."""
    m = _TYPE_RE.match(type_text or "")
    if not m:
        return "STRING", f"unrecognised type '{type_text}'"
    base, args = m.group(1).lower(), m.group(2)
    if base in ("decimal", "numeric", "dec"):
        nums = [a.strip() for a in (args or "18,0").split(",")]
        p = int(nums[0]) if nums[0].isdigit() else 18
        s = int(nums[1]) if len(nums) > 1 and nums[1].isdigit() else 0
        bq = "NUMERIC" if (p - s <= 29 and s <= 9) else "BIGNUMERIC"
        if parameterized:
            bq = f"{bq}({p}, {s})"
        return bq, None
    if base in SIMPLE_TYPES:
        bq = SIMPLE_TYPES[base]
        note = None
        if base in ("xml",):
            note = "XML mapped to STRING"
        if base in ("timestamp", "rowversion"):
            note = "rowversion has no equivalent; mapped to BYTES"
        if parameterized and bq == "STRING" and args and args.lower() != "max" and args.isdigit():
            bq = f"STRING({args})"
        return bq, note
    return "STRING", f"user-defined or unsupported type '{type_text.strip()}' mapped to STRING"


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def indent(lines: List[str], n: int = 2) -> List[str]:
    pad = " " * n
    return [pad + l if l.strip() else l for l in lines]


def comment_lines(text: str) -> List[str]:
    """Turn arbitrary text into '--' comment lines."""
    return ["-- " + l.rstrip() if l.strip() else "--" for l in text.strip().splitlines()]


def norm_comment(c: str) -> List[str]:
    if c.startswith("--"):
        return [c.rstrip()]
    return ["-- " + l.strip() for l in c[2:-2].strip().splitlines() if l.strip()] or []


_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def one_line(text: str) -> str:
    return " ".join(_ANSI.sub("", str(text or "")).split())


def todo_block(reason: str, original: str) -> List[str]:
    lines = [f"-- TODO(manual): {one_line(reason)}"]
    lines += ["--   " + l.rstrip() for l in original.strip().splitlines()]
    return lines


def qualify(parts: List[str]) -> Tuple[Optional[str], Optional[str], str]:
    """Split dotted name parts into (database, schema, object)."""
    if len(parts) >= 3:
        return parts[-3], parts[-2], parts[-1]
    if len(parts) == 2:
        return None, parts[0], parts[1]
    return None, None, parts[0]


def object_name(cfg: Config, schema: Optional[str], name: str) -> str:
    if schema and schema.lower() != "dbo" and cfg.schema_mode == "prefix":
        return f"{schema}_{name}"
    return name


def routine_name(cfg: Config, schema: Optional[str], name: str) -> str:
    """BigQuery routine names may only contain letters, digits and underscores."""
    return re.sub(r"[^A-Za-z0-9_]", "_", object_name(cfg, schema, name))


def registry_key(schema: Optional[str], name: str, folder: Optional[str] = None) -> str:
    key = f"{(schema or 'dbo').lower()}.{name.lower()}"
    return f"{folder.lower()}:{key}" if folder else key


def iter_tokens(node):
    """All tokens of a statement tree (Simple statements and IF/WHILE conditions)."""
    if isinstance(node, Block):
        for s in node.stmts:
            yield from iter_tokens(s)
    elif isinstance(node, Simple):
        yield from node.toks
    elif isinstance(node, If):
        yield from node.cond_toks
        yield from iter_tokens(node.then)
        if node.els is not None:
            yield from iter_tokens(node.els)
    elif isinstance(node, While):
        yield from node.cond_toks
        yield from iter_tokens(node.body)
    elif isinstance(node, TryCatch):
        yield from iter_tokens(node.try_)
        yield from iter_tokens(node.catch)


# T-SQL functions that sqlglot passes through unchanged but that do not exist in BigQuery
TSQL_ONLY_FUNCS = {
    "DATALENGTH": "use BYTE_LENGTH() (NVARCHAR counts 2 bytes per character in T-SQL)",
    "PATINDEX": "use REGEXP_INSTR() with a regular expression",
    "STR": "use FORMAT('%10.0f', x) / CAST(... AS STRING)",
    "NCHAR": "use CODE_POINTS_TO_STRING([n])",
    "STRING_SPLIT": "use UNNEST(SPLIT(s, sep)) AS value",
    "ISJSON": "use SAFE.PARSE_JSON(x) IS NOT NULL",
    "XACT_STATE": "no equivalent",
    "SWITCHOFFSET": "BigQuery TIMESTAMP has no offset; format with FORMAT_TIMESTAMP(..., time_zone)",
    "TODATETIMEOFFSET": "BigQuery TIMESTAMP has no offset", "DATETIMEOFFSETFROMPARTS": "no equivalent",
    "DATETIMEFROMPARTS": "use DATETIME(y, m, d, h, mi, s)", "DATETIME2FROMPARTS": "use DATETIME(y, m, d, h, mi, s)",
    "SMALLDATETIMEFROMPARTS": "use DATETIME(y, m, d, h, mi, 0)", "TIMEFROMPARTS": "use TIME(h, m, s)",
    "TRY_PARSE": "use SAFE.PARSE_DATE / SAFE_CAST", "PARSE": "use PARSE_DATE / PARSE_DATETIME / CAST",
    "DIFFERENCE": "no equivalent (SOUNDEX exists)", "FORMATMESSAGE": "use FORMAT()",
    "COL_LENGTH": "use INFORMATION_SCHEMA.COLUMNS", "COLUMNPROPERTY": "use INFORMATION_SCHEMA",
    "OBJECTPROPERTY": "use INFORMATION_SCHEMA", "SCHEMA_NAME": "no equivalent", "TYPE_NAME": "no equivalent",
    "USER_NAME": "use SESSION_USER()", "SESSION_CONTEXT": "no equivalent", "CONTEXT_INFO": "no equivalent",
    "COMPRESS": "no equivalent", "DECOMPRESS": "no equivalent", "CRYPT_GEN_RANDOM": "no equivalent",
    "CHECKSUM_AGG": "use BIT_XOR(FARM_FINGERPRINT(...))", "GROUPING_ID": "no equivalent; use GROUPING()",
    "SQUARE": "use POWER(x, 2)", "ATN2": "use ATAN2()", "DEGREES": "use x * 180 / ACOS(-1)",
    "RADIANS": "use x * ACOS(-1) / 180", "PI": "use ACOS(-1)", "STDEVP": "use STDDEV_POP()",
    "VARP": "use VAR_POP()", "STDEV": "use STDDEV_SAMP()", "VAR": "use VAR_SAMP()",
}


# --------------------------------------------------------------------------
# Translator
# --------------------------------------------------------------------------
class Translator:
    def __init__(self, cfg: Config):
        self.cfg = cfg

    # ---------------------------------------------------------- public ----
    def convert(self, proc: ProcDef, dataset: str, registry: Dict[str, ProcDef], folder: Optional[str] = None):
        ctx = Ctx(self.cfg, dataset, registry, proc_name=proc.name, folder=folder)
        self.collect_symbols(proc, ctx)
        if any(t.up == "XACT_STATE" or (t.kind == "var" and t.text.upper() == "@@TRANCOUNT")
               for t in iter_tokens(proc.body)):
            # BigQuery has no @@TRANCOUNT / XACT_STATE(): track the transaction state in a variable instead of
            # dropping the guard (an unguarded ROLLBACK fails when the error happened outside the transaction)
            ctx.tran_flag = f"{self.cfg.var_prefix}in_transaction"
            ctx.hoisted[ctx.tran_flag] = "BOOL DEFAULT FALSE"
            ctx.warn("info", f"@@TRANCOUNT / XACT_STATE() emulated with variable {ctx.tran_flag} (TRUE between "
                             f"BEGIN TRANSACTION and COMMIT/ROLLBACK of this procedure)")

        status_mode = (self.cfg.return_status_mode or "drop").lower()
        if status_mode == "out_param" and self.returns_status(proc):
            ctx.return_param = f"{self.cfg.param_prefix}return_status"
        body_lines = self.emit_block(proc.body, ctx)
        if ctx.return_param:
            body_lines = [f"SET {ctx.return_param} = 0;  -- T-SQL procedures return 0 unless RETURN <n> says otherwise"] \
                + body_lines

        # parameters -------------------------------------------------------
        param_sql, defaults = [], []
        for p in proc.params:
            bq, note = map_type(p.type_text)
            if p.readonly:
                struct = self.table_type(p.type_text)
                if struct:
                    param_sql.append(f"IN {self.cfg.param_prefix}{p.name} ARRAY<STRUCT<{struct}>>")
                    ctx.warn("info", f"table-valued parameter @{p.name} ({p.type_text.strip()}) -> "
                                     f"ARRAY<STRUCT<...>>; FROM @{p.name} reads UNNEST({self.cfg.param_prefix}{p.name})")
                else:
                    ctx.warn("review", f"table-valued parameter @{p.name}: type {p.type_text.strip()} is unknown - add "
                                       f"its CREATE TYPE ... AS TABLE file to the input folder or config 'table_types' "
                                       f"so it becomes ARRAY<STRUCT<...>>", p.type_text)
                    param_sql.append(f"-- TODO(manual): IN {self.cfg.param_prefix}{p.name} ARRAY<STRUCT<...>>  "
                                     f"-- T-SQL: @{p.name} {p.type_text.strip()} READONLY")
                continue
            if note:
                ctx.warn("review", f"parameter @{p.name}: {note}")
            mode = ("OUT" if (self.cfg.out_param_mode or "").upper() == "OUT" else "INOUT") if p.is_out else "IN"
            param_sql.append(f"{mode} {self.cfg.param_prefix}{p.name} {bq}")
            if p.default is not None:
                defaults.append(f"@{p.name} = {p.default}")
        if ctx.return_param:
            param_sql.append(f"OUT {ctx.return_param} INT64")
            ctx.warn("info", f"RETURN <n> status kept in OUT parameter {ctx.return_param} (return_status_mode = "
                             f"out_param): callers must pass a variable for it")
        for p in proc.params:
            if p.name.lower() in ctx.assigned and not p.is_out:
                ctx.warn("review", f"IN parameter @{p.name} is modified inside the procedure; "
                                   f"copy it to a local variable or make it INOUT")
        if proc.options:
            ctx.warn("info", f"procedure options dropped: {' '.join(proc.options.split())}")

        # declarations -----------------------------------------------------
        decls = [f"DECLARE {n} {t};" for n, t in ctx.hoisted.items()]

        name = routine_name(self.cfg, proc.schema, proc.name)
        if name != object_name(self.cfg, proc.schema, proc.name):
            ctx.warn("review", f"procedure name '{proc.name}' contains characters BigQuery does not allow in routine "
                               f"names; renamed to {name} (callers in the input set use the new name)")
        full = f"{self.cfg.project}.{dataset}.{name}"
        head = [
            f"-- Source: {proc.schema + '.' if proc.schema else ''}{proc.name} (SQL Server stored procedure)",
        ]
        for c in proc.header_comments:
            head += norm_comment(c)
        if defaults:
            head += ["-- Parameter defaults are not supported by BigQuery; callers must pass every argument:",
                     "--   " + ", ".join(defaults)]
        sig = "CREATE OR REPLACE PROCEDURE `%s`(" % full
        if param_sql:
            # keep comment-only entries out of the comma logic
            real = [i for i, s in enumerate(param_sql) if not s.startswith("--")]
            plines = []
            for i, s in enumerate(param_sql):
                comma = "," if (not s.startswith("--") and i != real[-1]) else ""
                plines.append("  " + s + comma)
            sig_lines = [sig] + plines + [")"]
        else:
            sig_lines = [sig + ")"]
        out = head + sig_lines + ["BEGIN"] + indent(decls + body_lines) + ["END;"]
        return "\n".join(out) + "\n", ctx.warnings, full

    def convert_view(self, view, dataset: str):
        """Translate a parsed CREATE VIEW into CREATE OR REPLACE VIEW."""
        ctx = Ctx(self.cfg, dataset, {})
        toks = view.query_toks
        text = self.render(toks, view.batch_src, 0, ctx)
        tree = self.parse_sql(text)
        self.inline_ctes(tree, ctx)
        self.postprocess(tree, ctx)
        body = self.finish(tree.sql("bigquery", pretty=True)).rstrip().rstrip(";")
        self.check_watchlist(Simple(toks, text, "SELECT"), ctx, body)
        if view.options.strip():
            ctx.warn("info", f"view options dropped: {' '.join(view.options.split())}")
        name = object_name(self.cfg, view.schema, view.name)
        full = f"{self.cfg.project}.{dataset}.{name}"
        head = [f"-- Source: {view.schema + '.' if view.schema else ''}{view.name} (SQL Server view)"]
        for c in view.header_comments:
            head += norm_comment(c)
        cols = f" ({', '.join(view.columns)})" if view.columns else ""
        out = head + [f"CREATE OR REPLACE VIEW `{full}`{cols} AS"] + body.splitlines()
        return "\n".join(out) + ";\n", ctx.warnings, full

    def table_type(self, type_text: str) -> Optional[str]:
        """STRUCT field list of a user-defined table type (config 'table_types' / CREATE TYPE ... AS TABLE)."""
        key = re.sub(r"[\[\]\s]", "", type_text or "").lower()
        types = {re.sub(r"[\[\]\s]", "", k).lower(): v for k, v in (self.cfg.table_types or {}).items()}
        return types.get(key) or types.get("dbo." + key) or (types.get(key.split(".", 1)[1]) if "." in key else None)

    # ---------------------------------------------------------- symbols ---
    def collect_symbols(self, proc: ProcDef, ctx: Ctx):
        cfg = self.cfg
        for p in proc.params:
            bq, _ = map_type(p.type_text)
            nm = cfg.param_prefix + p.name
            if p.readonly:                     # table-valued parameter: an ARRAY<STRUCT>, read with UNNEST()
                ctx.vars[p.name.lower()] = (f"UNNEST({nm})", "TVP")
                continue
            ctx.vars[p.name.lower()] = (nm, bq)
            if bq == "STRING":
                ctx.string_vars.add(nm.lower())
            if not p.is_out:
                ctx.params_in.add(p.name.lower())

        def walk(node):
            if isinstance(node, Block):
                for s in node.stmts:
                    walk(s)
            elif isinstance(node, Simple):
                dv = self.dyn_sql_var(node)
                if dv:
                    ctx.dyn_vars.add(dv)
                if node.kind == "DECLARE":
                    for d in self.parse_declare(node):
                        if d["cursor"]:
                            continue
                        nm = cfg.var_prefix + d["name"]
                        if d["table"] is not None:
                            nm = cfg.temp_prefix + d["name"]
                            ctx.vars[d["name"].lower()] = (nm, "TABLE")
                            ctx.temp_names.add(nm.lower())
                        elif self.table_type(d["type"]):        # DECLARE @t dbo.MyTableType -> temp table
                            nm = cfg.temp_prefix + d["name"]
                            ctx.vars[d["name"].lower()] = (nm, "TABLE")
                            ctx.temp_names.add(nm.lower())
                        else:
                            bq, _ = map_type(d["type"])
                            ctx.vars[d["name"].lower()] = (nm, bq)
                            if bq == "STRING":
                                ctx.string_vars.add(nm.lower())
            elif isinstance(node, If):
                walk(node.then)
                if node.els is not None:
                    walk(node.els)
            elif isinstance(node, While):
                walk(node.body)
            elif isinstance(node, TryCatch):
                walk(node.try_)
                walk(node.catch)

        walk(proc.body)

    @staticmethod
    def dyn_sql_var(node: Simple) -> Optional[str]:
        """Name of the variable executed by `EXEC sp_executesql @v, ...` or `EXEC (@v)`, if any."""
        t = node.toks
        if node.kind not in ("EXEC", "EXECUTE") or len(t) < 3:
            return None
        if t[1].is_p("(") and len(t) == 4 and t[2].kind == "var" and t[3].is_p(")"):
            return t[2].text.lstrip("@").lower()
        i = 1
        names = []
        while i < len(t) and t[i].kind in ("word", "bracket", "dquote"):
            names.append(ident_value(t[i]).lower())
            i += 1
            if i < len(t) and t[i].is_p("."):
                i += 1
            else:
                break
        if names and names[-1] == "sp_executesql" and i < len(t):
            first = _split_commas(t[i:])[0]
            if len(first) == 1 and first[0].kind == "var":
                return first[0].text.lstrip("@").lower()
        return None

    @staticmethod
    def is_cursor_decl(node: Simple) -> bool:
        t = node.toks
        return (node.kind == "DECLARE" and len(t) > 2 and t[1].kind != "var"
                and any(x.up == "CURSOR" for x in t[2:4]))

    def parse_declare(self, node: Simple) -> List[dict]:
        if self.is_cursor_decl(node):
            return [{"name": node.toks[1].text, "type": "", "init": None, "table": None, "cursor": True,
                     "toks": node.toks[1:]}]
        toks = node.toks[1:]
        out = []
        for g in _split_commas(toks):
            if not g:
                continue
            d = {"name": "", "type": "", "init": None, "table": None, "cursor": False, "toks": g}
            if g[0].kind == "var":
                d["name"] = g[0].text.lstrip("@")
                j = 1
                if j < len(g) and g[j].up == "AS":
                    j += 1
                if j < len(g) and g[j].up == "TABLE":
                    d["table"] = g[j + 1:]
                elif j < len(g) and g[j].up == "CURSOR":
                    d["cursor"] = True
                else:
                    depth, tt = 0, []
                    while j < len(g):
                        tk = g[j]
                        if tk.is_p("("):
                            depth += 1
                        elif tk.is_p(")"):
                            depth -= 1
                        if depth == 0 and tk.is_p("="):
                            break
                        tt.append(tk)
                        j += 1
                    d["type"] = self.slice(node, tt)
                    if j < len(g) and g[j].is_p("="):
                        d["init"] = g[j + 1:]
            else:
                d["cursor"] = True
                d["name"] = g[0].text
            out.append(d)
        return out

    @staticmethod
    def slice(node: Simple, toks: List[Token]) -> str:
        if not toks:
            return ""
        base = node.toks[0].start
        return node.text[toks[0].start - base: toks[-1].end - base]

    # ---------------------------------------------------------- rendering -
    def render(self, toks: List[Token], node_text: str, base: int, ctx: Ctx) -> str:
        """Source text of *toks* with variables / temp tables renamed."""
        if not toks:
            return ""
        out, pos = [], toks[0].start
        for tok in toks:
            out.append(node_text[pos - base: tok.start - base])
            pos = tok.end
            out.append(self.map_token(tok, ctx))
        return "".join(out)

    def render_simple(self, node: Simple, ctx: Ctx, toks=None) -> str:
        return self.render(toks if toks is not None else node.toks, node.text, node.toks[0].start, ctx)

    def map_token(self, tok: Token, ctx: Ctx) -> str:
        if tok.kind == "var":
            t = tok.text
            if t.startswith("@@"):
                u = t.upper()
                if u == "@@ROWCOUNT":
                    if not ctx.last_dml:
                        ctx.warn("review", "@@ROWCOUNT is read after a statement that is not INSERT/UPDATE/DELETE/MERGE: "
                                           "T-SQL counts rows of SELECT / SET / SELECT @v = ... too, but BigQuery "
                                           "@@row_count only reflects DML, so this check will not behave the same "
                                           "(e.g. after SELECT @v = col ... test `v IS NULL` or use a COUNT(*))")
                    return ROWCOUNT_PH
                if u == "@@TRANCOUNT" and ctx.tran_flag:
                    return f"(CASE WHEN {ctx.tran_flag} THEN 1 ELSE 0 END)"
                if u in WATCHLIST:
                    ctx.warn("review", f"{u}: {WATCHLIST[u]}")
                else:
                    ctx.warn("review", f"system variable {u} has no BigQuery equivalent")
                return t
            key = t[1:].lower()
            if key in ctx.vars:
                return ctx.vars[key][0]
            ctx.warn("review", f"variable {t} is not declared in this procedure")
            return self.cfg.var_prefix + t[1:]
        if tok.kind == "string" and tok.text[0] in "Nn":
            return tok.text[1:]          # N'..' prefix is not valid in BigQuery
        if tok.kind == "money":
            return tok.text.lstrip("$").strip()          # $12.50 money literal -> 12.50
        if tok.kind == "temp":
            nm = self.cfg.temp_prefix + tok.text.lstrip("#")
            ctx.temp_names.add(nm.lower())
            return nm
        return tok.text

    def check_watchlist(self, node: Simple, ctx: Ctx, out_text: Optional[str] = None):
        seen = set()
        toks = node.toks
        for k, tok in enumerate(toks):
            if tok.up == "NEXT" and k + 2 < len(toks) and toks[k + 1].up == "VALUE" and toks[k + 2].up == "FOR" \
                    and "NEXT" not in seen:
                seen.add("NEXT")
                ctx.warn("review", "NEXT VALUE FOR <sequence>: BigQuery has no sequences; use GENERATE_UUID() or "
                                   "ROW_NUMBER() + MAX(existing key)", node.text)
            if tok.kind == "temp" and tok.text.startswith("##") and "##" not in seen:
                seen.add("##")
                ctx.warn("info", f"global temp table {tok.text} becomes a script-local TEMP TABLE: other sessions "
                                 f"cannot see it in BigQuery")
        for tok in node.toks:
            u = tok.up
            if u in WATCHLIST and u not in seen:
                # OUTPUT as a keyword only matters for DML; EXEC args handled elsewhere
                if u == "OUTPUT" and node.kind in ("EXEC", "EXECUTE"):
                    continue
                if u in CONVERTIBLE and out_text is not None:
                    code = "\n".join(l for l in out_text.splitlines() if not l.lstrip().startswith("--"))
                    if not re.search(r"\b%s\b" % re.escape(u), code, re.I) and "TODO(manual)" not in out_text:
                        continue                # converted by v5 (or the FOR XML PATH idiom -> STRING_AGG)
                seen.add(u)
                if u in WATCHLIST_INFO:
                    ctx.warn("info", f"{u}: converted to BigQuery {u} syntax; compare the result columns once")
                    continue
                ctx.warn("review", f"{u}: {WATCHLIST[u]}")

    # ---------------------------------------------------------- SQL core --
    def parse_sql(self, text: str):
        tree = sqlglot.parse_one(text, read="tsql")
        if tree is None or isinstance(tree, exp.Command):
            raise ValueError("unsupported syntax")
        return tree

    def finish(self, sql: str) -> str:
        return (sql.replace(ROWCOUNT_PH, "@@row_count").replace(ERRMSG_PH, "@@error.message")
                .replace(ERRLINE_PH, "@@error.stack_trace[SAFE_OFFSET(0)].line"))

    def expr_sql(self, text: str, ctx: Ctx) -> str:
        tree = self.parse_sql("SELECT (" + text + ")")
        if not isinstance(tree, exp.Select) or len(tree.expressions) != 1:
            raise ValueError("not a single expression")
        self.postprocess(tree, ctx)
        node = tree.expressions[0]
        if isinstance(node, exp.Paren):
            node = node.this
        return self.finish(node.sql("bigquery"))

    def is_stringy(self, node, ctx: Ctx) -> bool:
        if isinstance(node, exp.Literal):
            return node.is_string
        if isinstance(node, exp.Paren):
            return self.is_stringy(node.this, ctx)
        if isinstance(node, exp.Column) and not node.table:
            return node.name.lower() in ctx.string_vars
        if isinstance(node, (exp.Cast, exp.TryCast)):
            to = node.args.get("to")
            return bool(to and to.is_type(*exp.DataType.TEXT_TYPES))
        if isinstance(node, STRING_CLASSES):
            return True
        if isinstance(node, exp.Add):
            return self.is_stringy(node.this, ctx) or self.is_stringy(node.expression, ctx)
        if isinstance(node, exp.Coalesce):
            return any(self.is_stringy(a, ctx) for a in [node.this] + list(node.expressions))
        return False

    # ---------------------------------------------------------- type helpers --
    def type_of(self, node, ctx: Ctx) -> Optional[str]:
        """Best-effort BigQuery temporal/bool type of an expression: date|datetime|timestamp|bool|None."""
        if isinstance(node, exp.Paren):
            return self.type_of(node.this, ctx)
        if isinstance(node, exp.CurrentDatetime):
            return "datetime"
        if isinstance(node, exp.CurrentTimestamp):
            return "timestamp"
        if isinstance(node, exp.CurrentDate):
            return "date"
        if isinstance(node, (exp.Cast, exp.TryCast)):
            to = node.args.get("to")
            if to is not None:
                kind = to.sql("bigquery").lower()
                if kind in ("date", "datetime", "timestamp"):
                    return kind
            return None
        if isinstance(node, (exp.DateAdd, exp.DatetimeAdd, exp.TimestampAdd)):
            return self.type_of(node.this, ctx)
        if isinstance(node, exp.Column):
            name = node.name.lower()
            if not node.table:
                for bq, typ in ctx.vars.values():
                    if bq.lower() == name:
                        return {"DATE": "date", "DATETIME": "datetime", "TIMESTAMP": "timestamp",
                                "BOOL": "bool"}.get(typ)
            return self.column_kind(name)
        return None

    def column_kind(self, name: str) -> Optional[str]:
        t = (self.col_types.get(name.lower()) or "").lower()
        if t == "bit":
            return "bool"
        if t == "date":
            return "date"
        if t in ("datetime", "datetime2", "smalldatetime"):
            return "datetime"
        if t in ("datetimeoffset", "timestamp_tz"):
            return "timestamp"
        return None

    @property
    def col_types(self) -> Dict[str, str]:
        if not hasattr(self, "_col_types"):
            d = {str(k).lower(): str(v) for k, v in (self.cfg.column_types or {}).items()}
            for c in self.cfg.bit_columns or []:
                d[str(c).lower()] = "bit"
            self._col_types = d
        return self._col_types

    # ---------------------------------------------------------- DML fixes ---
    def inline_ctes(self, tree, ctx: Ctx):
        """BigQuery has no WITH before UPDATE/DELETE/MERGE/INSERT: inline the CTEs as subqueries."""
        w = tree.args.get("with_")
        if w is None:
            return
        if isinstance(tree, exp.Insert):
            body = tree.args.get("expression")
            if isinstance(body, (exp.Select, exp.Union, exp.Subquery)) and body.args.get("with_") is None:
                body.set("with_", w)
                tree.set("with_", None)
            return
        if not isinstance(tree, (exp.Update, exp.Delete, exp.Merge)):
            return
        ctes = {}
        for c in w.expressions:
            ctes[c.alias.lower()] = c
        target = tree.this
        if isinstance(target, exp.Table) and not target.db and target.name.lower() in ctes:
            ctx.warn("review", "UPDATE/DELETE through a CTE is not supported in BigQuery; rewrite against the base table",
                     target.name)
        resolved: Dict[str, exp.Expression] = {}
        for name, c in ctes.items():
            body = c.this.copy()
            self._subst_ctes(body, resolved)
            resolved[name] = body
        tree.set("with_", None)
        self._subst_ctes(tree, resolved)
        ctx.warn("info", "CTE before UPDATE/DELETE/MERGE inlined as subquery (BigQuery has no WITH before DML)")

    @staticmethod
    def _subst_ctes(node, resolved):
        for tbl in list(node.find_all(exp.Table)):
            if tbl.db or not isinstance(tbl.this, exp.Identifier):
                continue
            body = resolved.get(tbl.name.lower())
            if body is None or tbl is node:
                continue
            alias = tbl.alias or tbl.name
            sub = exp.Subquery(this=body.copy(), alias=exp.TableAlias(this=exp.to_identifier(alias)))
            joins = tbl.args.get("joins")
            if joins:
                sub.set("joins", [j.copy() for j in joins])
            tbl.replace(sub)

    # ---------------------------------------------------------- value fixes -
    def fix_datetime(self, tree, ctx: Ctx):
        ts = (self.cfg.datetime_mode or "datetime").lower() == "timestamp"
        for n in list(tree.find_all(exp.CurrentTimestamp)):
            if not ts:
                n.replace(exp.CurrentDatetime())
        for n in list(tree.find_all(exp.CurrentTimestampLTZ)):
            n.replace(exp.CurrentTimestamp())
        for n in list(tree.find_all(exp.Anonymous)):
            if str(n.name).upper() in ("GETUTCDATE", "SYSUTCDATETIME"):
                n.replace(exp.CurrentTimestamp() if ts else exp.CurrentDatetime(this=exp.Literal.string("UTC")))
        # DATEADD(unit, DATEDIFF(unit, 0, x), 0): the classic T-SQL "truncate to unit" idiom -> DATETIME_TRUNC
        def is_zero_date(x):
            x = x.this if isinstance(x, exp.TimeStrToTime) else x
            return isinstance(x, exp.Literal) and str(x.this) in ("0", "1900-01-01")
        for n in list(tree.find_all(exp.DateAdd)):
            dd = n.expression
            if is_zero_date(n.this) and isinstance(dd, exp.DateDiff) and is_zero_date(dd.expression):
                u1 = (n.args.get("unit").name if n.args.get("unit") is not None else "DAY").upper()
                u2 = (dd.args.get("unit").name if dd.args.get("unit") is not None else "DAY").upper()
                if u1 == u2 and u1 in ("DAY", "MONTH", "YEAR", "QUARTER", "HOUR", "MINUTE"):
                    x = dd.this.this if isinstance(dd.this, exp.TimeStrToTime) else dd.this
                    x = x.copy() if self.type_of(x, ctx) == "datetime" else exp.cast(x.copy(), "datetime")
                    n.replace(exp.DatetimeTrunc(this=x, unit=exp.var(u1)))
                    ctx.warn("info", f"DATEADD({u1}, DATEDIFF({u1}, 0, x), 0) idiom converted to DATETIME_TRUNC(x, {u1})")
        # DATETRUNC(unit, x) -> pick DATE_TRUNC / DATETIME_TRUNC by type (sqlglot always writes TIMESTAMP_TRUNC)
        for n in list(tree.find_all(exp.TimestampTrunc)):
            kind = self.type_of(n.this, ctx)
            if kind == "datetime":
                n.replace(exp.DatetimeTrunc(this=n.this.copy(), unit=n.args.get("unit")))
            elif kind == "date":
                n.replace(exp.DateTrunc(this=n.this.copy(), unit=n.args.get("unit")))
        # DATEDIFF: DATE_DIFF only accepts DATE; DATETIME_DIFF counts boundaries like T-SQL and accepts DATE via CAST
        for n in list(tree.find_all(exp.DateDiff)):
            def inner(x):
                return x.this if isinstance(x, exp.TimeStrToTime) else x
            def as_dt(x):
                x = inner(x).copy()
                return x if self.type_of(x, ctx) == "datetime" else exp.cast(x, "datetime")
            n.replace(exp.DatetimeDiff(this=as_dt(n.this), expression=as_dt(n.expression), unit=n.args.get("unit")))
        # DATEADD: DATE_ADD only accepts DATE
        subday = {"HOUR", "MINUTE", "SECOND", "MILLISECOND", "MICROSECOND"}
        for n in list(tree.find_all(exp.DateAdd)):
            unit = n.args.get("unit")
            uname = (unit.name if unit is not None else "DAY").upper()
            kind = self.type_of(n.this, ctx)
            if kind == "timestamp":
                n.replace(exp.TimestampAdd(this=n.this.copy(), expression=n.expression.copy(), unit=unit))
            elif kind == "datetime" or (kind in (None, "date") and uname in subday):
                base = n.this.copy() if kind == "datetime" else exp.cast(n.this.copy(), "datetime")
                n.replace(exp.DatetimeAdd(this=base, expression=n.expression.copy(), unit=unit))
            elif kind is None:
                ctx.warn("info", "DATEADD on an expression of unknown type: DATE_ADD only accepts DATE - if it is "
                                 "DATETIME/TIMESTAMP use DATETIME_ADD/TIMESTAMP_ADD (or list the column in config "
                                 "'column_types')")
        # FORMAT(date, 'fmt') / CONVERT(VARCHAR, date, style): FORMAT_DATE only accepts DATE, so pick by type
        for n in list(tree.find_all(exp.TimeToStr)):
            fmt = n.args.get("format")
            if isinstance(fmt, exp.Literal):
                n.replace(self.format_dt(n.this.copy(), fmt.copy(), ctx))
        for n in list(tree.find_all(exp.Convert)):
            style = n.args.get("style")
            dtype = n.this
            if not isinstance(style, exp.Literal) or style.is_string:
                continue
            code = int(float(style.this))
            fmt = CONVERT_STYLES.get(code)
            text_type = isinstance(dtype, exp.DataType) and dtype.is_type(*exp.DataType.TEXT_TYPES)
            if fmt is None or not text_type:
                if text_type:
                    ctx.warn("review", f"CONVERT style {code} is not translated automatically; use FORMAT_DATETIME(...)")
                continue
            out = self.format_dt(n.expression.copy(), exp.Literal.string(fmt), ctx)
            params = dtype.expressions
            if params and isinstance(params[0].this, exp.Literal) and str(params[0].this.this).isdigit():
                out = exp.Substring(this=out, start=exp.Literal.number(1), length=exp.Literal.number(params[0].this.this))
            n.replace(out)
        # YEAR()/MONTH()/DAY() do not exist in BigQuery -> EXTRACT(part FROM x)
        for cls, part in ((exp.Year, "YEAR"), (exp.Month, "MONTH"), (exp.Day, "DAY")):
            for n in list(tree.find_all(cls)):
                arg = n.this
                if isinstance(arg, exp.TsOrDsToDate):
                    inner_arg = arg.this
                    # keep the CAST only for text input (T-SQL converts strings implicitly)
                    arg = exp.cast(inner_arg.copy(), "date") if (isinstance(inner_arg, exp.Literal) and inner_arg.is_string) \
                        else inner_arg.copy()
                else:
                    arg = arg.copy()
                n.replace(exp.Extract(this=exp.var(part), expression=arg))
        self.fix_variable_limit(tree, ctx)
        for lim in list(tree.find_all(exp.Limit)):
            opts = lim.args.get("limit_options")
            val = lim.args.get("expression")
            if opts is not None and opts.args.get("percent") and not opts.args.get("with_ties") \
                    and isinstance(val, exp.Literal) and str(val.this) in ("100", "100.0"):
                lim.pop()                                   # TOP 100 PERCENT (view idiom) returns every row
                ctx.warn("info", "TOP 100 PERCENT removed (it returns all rows; an ORDER BY in a view does not "
                                 "guarantee the order of the result)")
                continue
            if opts is not None and (opts.args.get("percent") or opts.args.get("with_ties")):
                self.top_percent_ties(lim, val, opts, ctx)
        for pv in tree.find_all(exp.Pivot):
            if pv.args.get("unpivot"):          # UNPIVOT (v FOR c IN (A, B)): A, B are columns - keep them
                continue
            for f in pv.args.get("fields") or []:
                if isinstance(f, exp.In):
                    vals = []
                    for v in f.expressions:
                        if isinstance(v, exp.Column) and isinstance(v.this, exp.Identifier):
                            vals.append(exp.alias_(exp.Literal.string(v.name), exp.to_identifier(v.name)))
                        else:
                            vals.append(v)
                    f.set("expressions", vals)

    def top_percent_ties(self, lim, val, opts, ctx: Ctx):
        """TOP n WITH TIES -> QUALIFY RANK() OVER (ORDER BY ...) <= n ;
        TOP p PERCENT -> QUALIFY ROW_NUMBER() OVER (ORDER BY ...) <= CEIL(COUNT(*) OVER () * p / 100)  (T-SQL rounds up)."""
        sel = lim.parent
        ties, pct = bool(opts.args.get("with_ties")), bool(opts.args.get("percent"))
        order = sel.args.get("order") if isinstance(sel, exp.Select) else None
        if not isinstance(sel, exp.Select) or val is None or (ties and order is None) \
                or sel.args.get("distinct") is not None:
            ctx.warn("review", "TOP ... PERCENT / WITH TIES (with DISTINCT or without ORDER BY) is not converted; "
                               "rewrite with RANK() / ROW_NUMBER() and QUALIFY", sel.sql("tsql"))
            return
        fn = exp.Rank() if ties else exp.RowNumber()
        win = exp.Window(this=fn, order=exp.Order(expressions=[o.copy() for o in order.expressions]) if order else None)
        limit_val = val.this if isinstance(val, exp.Paren) else val
        if pct:
            limit_val = fixes_v5.tmpl("CEIL(COUNT(*) OVER () * __a0__ / 100.0)", limit_val)
        cond = exp.LTE(this=win, expression=limit_val.copy())
        q = sel.args.get("qualify")
        sel.set("qualify", exp.Qualify(this=exp.and_(q.this.copy(), cond) if q is not None else cond))
        sel.set("limit", None)
        if sel.args.get("where") is None and sel.args.get("group") is None and sel.args.get("having") is None:
            sel.set("where", exp.Where(this=exp.true()))
        what = ("TOP n PERCENT WITH TIES" if pct and ties else "TOP n PERCENT" if pct else "TOP n WITH TIES")
        ctx.warn("info", f"{what} converted to QUALIFY {'RANK' if ties else 'ROW_NUMBER'}() OVER (ORDER BY ...)"
                         f"{' <= CEIL(COUNT(*) OVER () * p / 100)' if pct else ' <= n'}")

    def fix_variable_limit(self, tree, ctx: Ctx):
        """TOP (@n): BigQuery LIMIT only accepts a constant (literal or query parameter), not a script variable.
        Rewrite as QUALIFY ROW_NUMBER() OVER (ORDER BY <same order>) <= n."""
        def const(v):
            return v is None or v.find(exp.Column) is None

        for sel in list(tree.find_all(exp.Select)):
            lim, off = sel.args.get("limit"), sel.args.get("offset")
            # OFFSET @x ROWS FETCH NEXT @n ROWS ONLY: BigQuery LIMIT/OFFSET need constants
            cnt = lim.args.get("count") if isinstance(lim, exp.Fetch) else (lim.args.get("expression") if lim else None)
            offv = off.args.get("expression") if off is not None else None
            if offv is not None and not (const(cnt) and const(offv)):
                order = sel.args.get("order")
                rn = exp.Window(this=exp.RowNumber(),
                                order=exp.Order(expressions=[o.copy() for o in order.expressions]) if order else None)
                cond = exp.GT(this=rn, expression=exp.paren(offv.copy()))
                if cnt is not None:
                    cond = exp.and_(cond, exp.LTE(this=rn.copy(), expression=exp.Add(this=exp.paren(offv.copy()),
                                                                                       expression=exp.paren(cnt.copy()))))
                q = sel.args.get("qualify")
                sel.set("qualify", exp.Qualify(this=exp.and_(q.this.copy(), cond) if q is not None else cond))
                sel.set("limit", None)
                sel.set("offset", None)
                if sel.args.get("where") is None and sel.args.get("group") is None and sel.args.get("having") is None:
                    sel.set("where", exp.Where(this=exp.true()))
                ctx.warn("info", "OFFSET/FETCH with variables converted to QUALIFY ROW_NUMBER() OVER (ORDER BY ...) "
                                 "(BigQuery LIMIT/OFFSET accept only constants)")
                continue
            if isinstance(lim, exp.Fetch) and not const(cnt):
                lim = exp.Limit(expression=cnt.copy())
                sel.set("limit", lim)
            if lim is None:
                continue
            val = lim.args.get("expression")
            if val is None or isinstance(val, exp.Literal) or (isinstance(val, exp.Paren) and isinstance(val.this, exp.Literal)):
                continue
            if val.find(exp.Column) is None:          # constant expression such as 5 * 2: fine
                continue
            opts = lim.args.get("limit_options")
            if opts is not None and (opts.args.get("percent") or opts.args.get("with_ties")):
                continue                              # flagged separately
            if sel.args.get("distinct") is not None:
                ctx.warn("review", "TOP (variable) with DISTINCT: BigQuery LIMIT needs a constant; rewrite manually "
                                   "(e.g. wrap the DISTINCT query in a subquery and use QUALIFY ROW_NUMBER() there)",
                         sel.sql("bigquery"))
                continue
            order = sel.args.get("order")
            win = exp.Window(this=exp.RowNumber(),
                             order=exp.Order(expressions=[o.copy() for o in order.expressions]) if order else None)
            cond = exp.LTE(this=win, expression=val.copy())
            q = sel.args.get("qualify")
            sel.set("qualify", exp.Qualify(this=exp.and_(q.this.copy(), cond) if q is not None else cond))
            sel.set("limit", None)
            if sel.args.get("where") is None and sel.args.get("group") is None and sel.args.get("having") is None:
                sel.set("where", exp.Where(this=exp.true()))   # older BigQuery releases require WHERE with QUALIFY
            ctx.warn("info", "TOP (variable) converted to QUALIFY ROW_NUMBER() ... <= n (BigQuery LIMIT accepts only a "
                             "constant, not a variable)")

    def format_dt(self, value, fmt, ctx: Ctx):
        kind = self.type_of(value, ctx)
        if kind == "date":
            return exp.Anonymous(this="FORMAT_DATE", expressions=[fmt, value])
        if kind == "timestamp":
            return exp.Anonymous(this="FORMAT_TIMESTAMP", expressions=[fmt, value])
        if kind != "datetime":
            value = exp.cast(value, "datetime")
        return exp.Anonymous(this="FORMAT_DATETIME", expressions=[fmt, value])

    def fix_bits(self, tree, ctx: Ctx):
        """BIT is BOOL in BigQuery: `flag = 1` / `SET flag = 0` must become TRUE / FALSE."""
        def lit01(n):
            return isinstance(n, exp.Literal) and not n.is_string and n.this in ("0", "1")

        def as_bool(n):
            return exp.true() if n.this == "1" else exp.false()

        def boolish(col):
            return isinstance(col, exp.Column) and self.type_of(col, ctx) == "bool"

        hint_re = re.compile(r"^(is|has|can|was|should|allow|enable|disable|flag|active|deleted|archiv)|"
                             r"(reviewed|processed|approved|completed|enabled|disabled|flag)$", re.I)
        for node in list(tree.find_all(exp.EQ, exp.NEQ)):
            a, b = node.this, node.expression
            if boolish(a) and lit01(b):
                node.set("expression", as_bool(b))
            elif boolish(b) and lit01(a):
                node.set("this", as_bool(a))
            else:
                for col, lit in ((a, b), (b, a)):
                    if isinstance(col, exp.Column) and lit01(lit) and hint_re.match(col.name) \
                            and col.name.lower() not in ctx.bit_hints and col.name.lower() not in self.col_types \
                            and not (not col.table and self.type_of(col, ctx)):
                        ctx.bit_hints.add(col.name.lower())
                        ctx.warn("review", f"column {col.name} is compared with {lit.this}: if it is a BIT column (BOOL in "
                                           f"BigQuery) use TRUE/FALSE - list it in config 'bit_columns' (or its real type "
                                           f"in 'column_types') to convert/silence this automatically")
        def bool_value(v):
            """Turn 0/1 (also inside CASE/IIF branches) into FALSE/TRUE."""
            if lit01(v):
                v.replace(as_bool(v))
            elif isinstance(v, exp.Case):
                for i in v.args.get("ifs") or []:
                    if lit01(i.args.get("true")):
                        i.set("true", as_bool(i.args["true"]))
                d = v.args.get("default")
                if lit01(d):
                    v.set("default", as_bool(d))
            elif isinstance(v, exp.If):
                for k in ("true", "false"):
                    if lit01(v.args.get(k)):
                        v.set(k, as_bool(v.args[k]))
            elif isinstance(v, exp.Paren):
                bool_value(v.this)

        for eq in self.assignment_eqs(tree):           # UPDATE ... SET flag = CASE WHEN ... THEN 1 ELSE 0 END
            if boolish(eq.this):
                bool_value(eq.expression)
        for node in list(tree.find_all(exp.In)):        # flag IN (1)
            if boolish(node.this):
                for v in node.expressions:
                    if lit01(v):
                        v.replace(as_bool(v))
        for node in list(tree.find_all(exp.EQ, exp.NEQ)):   # @flag = 'true'
            for col, lit in ((node.this, node.expression), (node.expression, node.this)):
                if (boolish(col) or (isinstance(col, exp.Column) and self.type_of(col, ctx) == "bool")) \
                        and isinstance(lit, exp.Literal) and lit.is_string and str(lit.this).lower() in ("true", "false"):
                    lit.replace(exp.true() if str(lit.this).lower() == "true" else exp.false())
        for ins in tree.find_all(exp.Insert):
            sch, vals = ins.this, ins.args.get("expression")
            if isinstance(sch, exp.Schema):
                names = [c.name for c in sch.expressions]
                if isinstance(vals, exp.Values):
                    rows = [t.expressions for t in vals.expressions if isinstance(t, exp.Tuple)]
                elif isinstance(vals, exp.Select):           # INSERT INTO t (flag, ...) SELECT 1, ...
                    rows = [[e.this if isinstance(e, exp.Alias) else e for e in vals.expressions]]
                else:
                    rows = []
                for row in rows:
                    if len(row) == len(names):
                        for i, v in enumerate(list(row)):
                            if self.column_kind(names[i]) == "bool":
                                bool_value(v)

    # ------------------------------------------------- integer division ----
    _INT_T = {"int", "integer", "bigint", "smallint", "tinyint"}
    _NUM_T = {"decimal", "numeric", "float", "real", "money", "smallmoney"}

    def num_kind(self, node, ctx: Ctx) -> Optional[str]:
        """'int' | 'num' (decimal/float) | None (unknown) for a numeric expression."""
        if isinstance(node, (exp.Paren, exp.Neg)):
            return self.num_kind(node.this, ctx)
        if isinstance(node, exp.Literal):
            if node.is_string:
                return None
            txt = str(node.this).lower()
            return "num" if ("." in txt or "e" in txt) else "int"
        if isinstance(node, (exp.Cast, exp.TryCast)):
            to = node.args.get("to")
            kind = to.sql("bigquery").upper() if to is not None else ""
            if kind == "INT64":
                return "int"
            if kind in ("NUMERIC", "BIGNUMERIC", "FLOAT64") or kind.startswith(("NUMERIC", "BIGNUMERIC")):
                return "num"
            return None
        if isinstance(node, exp.Column):
            name = node.name.lower()
            if not node.table:
                for bq, typ in ctx.vars.values():
                    if bq.lower() == name:
                        return {"INT64": "int", "NUMERIC": "num", "BIGNUMERIC": "num",
                                "FLOAT64": "num"}.get(typ)
            t = (self.col_types.get(name) or "").lower()
            if t in self._INT_T:
                return "int"
            if t in self._NUM_T:
                return "num"
            return None
        if isinstance(node, (exp.Count, exp.Length, exp.DatetimeDiff, exp.DateDiff, exp.RowNumber)):
            return "int"
        if isinstance(node, exp.Anonymous) and str(node.name).upper() == "DIV":
            return "int"
        if isinstance(node, (exp.Sum, exp.Min, exp.Max, exp.Abs)):     # SUM(int) is int in T-SQL
            return self.num_kind(node.this, ctx)
        if isinstance(node, exp.Window):
            return self.num_kind(node.this, ctx)
        if isinstance(node, (exp.Add, exp.Sub, exp.Mul, exp.Mod)):
            a, b = self.num_kind(node.this, ctx), self.num_kind(node.expression, ctx)
            if "num" in (a, b):
                return "num"
            return "int" if a == b == "int" else None
        return None

    def fix_integer_division(self, tree, ctx: Ctx):
        """T-SQL `int / int` is integer division (5/2 = 2); BigQuery `/` always returns FLOAT64 (2.5)."""
        for node in reversed(list(tree.find_all(exp.Div))):      # innermost first
            a, b = self.num_kind(node.this, ctx), self.num_kind(node.expression, ctx)
            if "num" in (a, b):
                continue                                           # decimal/float arithmetic: `/` is right
            if a == b == "int":
                node.replace(exp.Anonymous(this="DIV", expressions=[node.this.copy(), node.expression.copy()]))
                continue
            text = node.sql("bigquery")
            if text not in ctx.div_hints:
                ctx.div_hints.add(text)
                ctx.warn("review", f"division `{text}`: operand types are unknown. T-SQL does INTEGER division when "
                                   f"both operands are integers (5/2 = 2) but BigQuery '/' always returns FLOAT64 "
                                   f"(2.5) - use DIV(a, b) if both are integers (or list the columns in config "
                                   f"'column_types' so this is converted automatically)", text)

    # ------------------------------------------- case-sensitive strings -----
    def fix_string_case(self, tree, ctx: Ctx):
        """SQL Server (default collation) compares strings case-insensitively, BigQuery does not."""
        mode = (self.cfg.case_sensitivity or "warn").lower()
        if mode == "off":
            return

        def text_lit(n):
            return (isinstance(n, exp.Literal) and n.is_string and re.search(r"[A-Za-z]", str(n.this))
                    and not re.match(r"^\d{4}-\d{2}-\d{2}", str(n.this)))

        def flag(col):
            key = col.name.lower()
            if key not in ctx.ci_hints:
                ctx.ci_hints.add(key)
                ctx.warn("review", f"string column {col.name} is compared with a text literal: SQL Server's default "
                                   f"collation is case-insensitive but BigQuery is case-sensitive, so 'NEW' will not "
                                   f"match 'new'. Confirm the data's casing is consistent, or set config "
                                   f"'case_sensitivity': 'lower' to wrap both sides in LOWER()", col.sql("bigquery"))

        def lower(n):
            return exp.Lower(this=n.copy())

        assigns = {id(e) for e in self.assignment_eqs(tree)}
        for node in list(tree.find_all(exp.EQ, exp.NEQ, exp.Like)):
            if id(node) in assigns:            # UPDATE ... SET col = 'x' is an assignment, not a comparison
                continue
            if node.meta.get("tsql2bq_cs"):    # explicit case-sensitive COLLATE: BigQuery already compares that way
                continue
            a, b = node.this, node.expression
            for col, lit in ((a, b), (b, a)):
                if isinstance(col, exp.Column) and text_lit(lit):
                    if mode == "lower":
                        node.set("this", lower(a))
                        node.set("expression", lower(b))
                    else:
                        flag(col)
                    break
        for node in list(tree.find_all(exp.In)):
            col, vals = node.this, node.expressions
            if isinstance(col, exp.Column) and vals and any(text_lit(v) for v in vals):
                if mode == "lower":
                    node.set("this", lower(col))
                    node.set("expressions", [lower(v) for v in vals])
                else:
                    flag(col)

    # ------------------------------------------------- v4 additions --------
    @staticmethod
    def assignment_eqs(tree) -> list:
        """EQ nodes that are SET assignments of UPDATE / MERGE ... WHEN MATCHED THEN UPDATE."""
        out = []
        for upd in ([tree] if isinstance(tree, exp.Update) else []) + list(tree.find_all(exp.Update)):
            out += [e for e in upd.expressions if isinstance(e, exp.EQ)]
        return out

    @staticmethod
    def like_to_regex(pat: str, esc: Optional[str]) -> str:
        """T-SQL LIKE pattern (with [..] character classes) -> anchored RE2 pattern."""
        out, i, n = ["(?s)^"], 0, len(pat)
        while i < n:
            c = pat[i]
            if esc and c == esc and i + 1 < n:
                out.append(re.escape(pat[i + 1]))
                i += 2
                continue
            if c == "%":
                out.append(".*")
            elif c == "_":
                out.append(".")
            elif c == "[":
                j = pat.find("]", i + 2 if pat[i + 1:i + 2] == "]" else i + 1)
                if j == -1:
                    out.append(re.escape(c))
                else:
                    inner = pat[i + 1:j]
                    neg = inner.startswith("^")
                    if neg:
                        inner = inner[1:]
                    body = inner.replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
                    out.append("[" + ("^" if neg else "") + body + "]")
                    i = j
            else:
                out.append(re.escape(c))
            i += 1
        return "".join(out) + "$"

    def fix_like(self, tree, ctx: Ctx):
        """LIKE differences: [..] character classes and ESCAPE do not exist in BigQuery LIKE, and a backslash is
        an escape character in BigQuery LIKE patterns but a plain character in T-SQL."""
        mode = (self.cfg.case_sensitivity or "warn").lower()
        done = set()
        for node in list(tree.find_all(exp.Escape)):
            like, esc = node.this, node.expression
            if isinstance(like, exp.Like) and isinstance(esc, exp.Literal) and esc.is_string \
                    and isinstance(like.expression, exp.Literal) and like.expression.is_string:
                p0 = str(like.expression.this)
                if re.search(r"\[[^\]]+\]", p0):              # classes + ESCAPE -> regular expression
                    rx = ("(?i)" if mode == "lower" else "") + self.like_to_regex(p0, str(esc.this))
                    node.replace(exp.RegexpLike(this=like.this.copy(), expression=exp.Literal.string(rx)))
                    continue
                like.set("expression", exp.Literal.string(self._like_pattern(p0, str(esc.this))))
                done.add(id(like))
                node.replace(like)
            else:
                ctx.warn("review", "LIKE ... ESCAPE is not supported by BigQuery LIKE (escape with a backslash instead)",
                         node.sql("bigquery"))
        for node in list(tree.find_all(exp.Like)):
            if id(node) in done:
                continue
            pat = node.expression
            if not (isinstance(pat, exp.Literal) and pat.is_string):
                if isinstance(pat, exp.Column) or pat.find(exp.Column):
                    ctx.warn("info", "LIKE with a non-constant pattern: T-SQL [..] character classes and backslashes "
                                     "behave differently in BigQuery LIKE")
                continue
            p = str(pat.this)
            if re.search(r"\[[^\]]+\]", p):
                simple = re.fullmatch(r"(?:[^\[\]]|\[[_%\[]\])*", p)
                if simple:     # '[_]', '[%]', '[[]' are only used to escape one character
                    p2 = re.sub(r"\[([_%\[])\]", lambda m: "\\" + m.group(1) if m.group(1) != "[" else "[",
                                p.replace("\\", "\\\\"))
                    node.set("expression", exp.Literal.string(p2))
                    continue
                rx = self.like_to_regex(p, None)
                if mode == "lower":
                    rx = "(?i)" + rx
                node.replace(exp.RegexpLike(this=node.this.copy(), expression=exp.Literal.string(rx)))
                ctx.warn("info", f"LIKE '{p}' uses T-SQL character classes, converted to REGEXP_CONTAINS")
            elif "\\" in p:
                node.set("expression", exp.Literal.string(p.replace("\\", "\\\\")))

    @staticmethod
    def _like_pattern(p: str, esc: str) -> str:
        out, i = [], 0
        while i < len(p):
            c = p[i]
            if c == esc and i + 1 < len(p):
                out.append("\\" + p[i + 1])
                i += 2
                continue
            out.append("\\\\" if c == "\\" else c)
            i += 1
        return "".join(out)

    def fix_for_xml(self, tree, ctx: Ctx):
        """FOR XML PATH('') string aggregation idiom -> STRING_AGG; any other FOR XML / FOR JSON is flagged
        (sqlglot silently drops the clause, which changes the result completely)."""
        def path_empty(sel):
            fc = sel.args.get("for_")
            if fc is None or str(fc.args.get("kind", "")).upper() != "XML":
                return False
            txt = fc.sql("tsql").upper().replace(" ", "")
            return "PATH('')" in txt and "TYPE" not in txt and "ROOT" not in txt

        def agg_of(sel, strip_sep=None):
            if len(sel.expressions) != 1:
                return None
            e = sel.expressions[0]
            e = e.this if isinstance(e, exp.Alias) else e
            sep = ""
            if strip_sep is not None:
                if not (isinstance(e, (exp.Add, exp.DPipe)) and isinstance(e.this, exp.Literal) and e.this.is_string
                        and len(str(e.this.this)) == strip_sep):
                    return None
                sep, e = str(e.this.this), e.expression
            order = sel.args.get("order")
            arg = exp.Order(this=e.copy(), expressions=[o.copy() for o in order.expressions]) if order else e.copy()
            new = sel.copy()
            new.set("for_", None)
            new.set("order", None)
            new.set("expressions", [exp.GroupConcat(this=arg, separator=exp.Literal.string(sep))])
            return new

        for fn in list(tree.find_all(exp.Stuff)):             # STUFF((SELECT ',' + x ... FOR XML PATH('')), 1, n, '')
            sub, start, ln, rep = fn.this, fn.args.get("start"), fn.args.get("length"), fn.expression
            if not all(isinstance(x, exp.Literal) for x in (start, ln, rep)):
                continue
            inner = sub.this if isinstance(sub, exp.Subquery) else None
            if isinstance(inner, exp.Select) and path_empty(inner) and str(start.this) == "1" \
                    and isinstance(ln, exp.Literal) and str(ln.this).isdigit() \
                    and isinstance(rep, exp.Literal) and rep.is_string and rep.this == "":
                new = agg_of(inner, strip_sep=int(ln.this))
                if new is not None:
                    fn.replace(exp.Subquery(this=new))
                    ctx.warn("info", "STUFF((SELECT sep + x ... FOR XML PATH('')), 1, n, '') converted to STRING_AGG "
                                     "(note: FOR XML also escaped &, <, > as &amp; &lt; &gt; - STRING_AGG does not)")
        for sel in list(tree.find_all(exp.Select)):
            if sel.args.get("for_") is None:
                continue
            if path_empty(sel) and isinstance(sel.parent, exp.Subquery):
                new = agg_of(sel)
                if new is not None:
                    sel.replace(new)
                    ctx.warn("info", "(SELECT ... FOR XML PATH('')) converted to STRING_AGG(..., '')")
                    continue
            kind = str(sel.args["for_"].args.get("kind", "")).upper() or "XML/JSON"
            if kind == "JSON" and self.for_json(sel, tree, ctx):
                continue
            ctx.warn("review", f"FOR {kind} has no BigQuery equivalent and was DROPPED - the query now returns rows "
                               f"instead of one {kind} string. Use STRING_AGG / TO_JSON_STRING(ARRAY_AGG(STRUCT(...)))",
                     sel.sql("bigquery"))
            sel.set("for_", None)

    def for_json(self, sel: exp.Select, tree, ctx: Ctx) -> bool:
        """SELECT ... FOR JSON PATH [, ROOT('r')] [, INCLUDE_NULL_VALUES] [, WITHOUT_ARRAY_WRAPPER]
        -> TO_JSON_STRING(ARRAY(SELECT AS STRUCT ...)).  Returns False when the shape is not handled."""
        opts = {}
        for o in sel.args["for_"].expressions:
            key = (o.this.name if isinstance(o, exp.QueryOption) and o.this is not None else o.name).upper()
            opts[key] = o.args.get("expression") if isinstance(o, exp.QueryOption) else None
        if "AUTO" in opts and (sel.args.get("joins") or not sel.args.get("from_")):
            return False                               # AUTO nests joined tables: needs a manual design
        if any("." in (e.alias or "") for e in sel.expressions if isinstance(e, exp.Alias)):
            ctx.warn("review", "FOR JSON PATH with dotted aliases ('a.b') builds nested objects: convert with "
                               "STRUCT(... AS b) AS a manually", sel.sql("tsql"))
            return False
        inner = sel.copy()
        inner.set("for_", None)
        arr = fixes_v5._array_of_struct(inner)
        js = exp.Anonymous(this="TO_JSON_STRING", expressions=[arr])
        root = opts.get("ROOT")
        if "ROOT" in opts:
            rname = str(root.this) if isinstance(root, exp.Literal) else "root"
            js = fixes_v5.tmpl(f"TO_JSON_STRING(STRUCT(__a0__ AS `{rname}`))", arr)
        elif "WITHOUT_ARRAY_WRAPPER" in opts:
            js = fixes_v5.tmpl("REGEXP_REPLACE(__a0__, r'^\\[|\\]$', '')", js)
        # T-SQL returns NULL (not '[]') for an empty result
        result = exp.Nullif(this=js, expression=exp.Literal.string("[]" if "ROOT" not in opts and
                                                                         "WITHOUT_ARRAY_WRAPPER" not in opts else ""))
        if "ROOT" in opts:
            result = js
        parent = sel.parent
        if isinstance(parent, exp.Subquery):
            parent.replace(exp.paren(result))
        elif sel is tree:
            new = exp.Select(expressions=[exp.alias_(result, "json")])
            for k in list(sel.args):
                sel.set(k, None)
            for k, v in new.args.items():
                sel.set(k, v)
        else:
            return False
        note = "" if "INCLUDE_NULL_VALUES" in opts else (" - NULL columns appear as \"col\":null in BigQuery but are "
                                                         "omitted by FOR JSON (add INCLUDE_NULL_VALUES semantics or "
                                                         "strip them if consumers care)")
        ctx.warn("info", "FOR JSON PATH -> TO_JSON_STRING(ARRAY(SELECT AS STRUCT ...))" + note)
        return True

    def fix_functions(self, tree, ctx: Ctx):
        # ROUND(x, n, 1) truncates in T-SQL; BigQuery's 3rd ROUND argument is a rounding MODE string
        for n in list(tree.find_all(exp.Round)):
            tr = n.args.get("truncate")
            if tr is not None:
                if isinstance(tr, exp.Literal) and str(tr.this) == "0":
                    n.set("truncate", None)
                else:
                    n.replace(exp.Anonymous(this="TRUNC", expressions=[n.this.copy()] +
                                            ([n.args["decimals"].copy()] if n.args.get("decimals") else [])))
        # CONVERT without a (handled) style -> CAST.  sqlglot writes it as SAFE_CAST, which turns bad data into NULL
        # instead of raising an error like T-SQL CONVERT does.
        for n in list(tree.find_all(exp.Convert)):
            to, val, style = n.this, n.expression, n.args.get("style")
            if not isinstance(to, exp.DataType):
                continue
            temporal = to.is_type(exp.DataType.Type.DATE, exp.DataType.Type.DATETIME, exp.DataType.Type.DATETIME2,
                                  exp.DataType.Type.TIMESTAMP, exp.DataType.Type.SMALLDATETIME)
            safe = bool(n.args.get("safe"))                       # TRY_CONVERT: NULL instead of an error
            if style is not None and isinstance(style, exp.Literal) and not style.is_string and temporal:
                code = int(float(style.this))
                target = "DATE" if to.is_type(exp.DataType.Type.DATE) else "DATETIME"
                if code in (20, 21, 23, 120, 121, 126, 127):          # ISO formats: CAST understands them
                    n.replace(exp.TryCast(this=val.copy(), to=exp.DataType.build(target.lower())) if safe
                              else exp.cast(val.copy(), target.lower()))
                elif code in CONVERT_STYLES:
                    fmt = CONVERT_STYLES[code].replace("%E3S", "%E*S")
                    n.replace(exp.Anonymous(this=f"{'SAFE.' if safe else ''}PARSE_{target}",
                                            expressions=[exp.Literal.string(fmt), val.copy()]))
                else:
                    ctx.warn("review", f"CONVERT(..., style {code}) to a date type is not translated; use PARSE_DATETIME")
                continue
            if style is not None and not isinstance(style, exp.Literal):
                ctx.warn("review", "CONVERT with a non-constant style is not translated", n.sql("tsql"))
                continue
            n.replace((exp.TryCast if safe else exp.Cast)(this=val.copy(), to=to.copy()))
        # CAST(decimal/float AS INT): T-SQL truncates (2.7 -> 2), BigQuery rounds (2.7 -> 3)
        for n in list(tree.find_all(exp.Cast, exp.TryCast)):
            to = n.args.get("to")
            if isinstance(to, exp.DataType) and to.is_type(*exp.DataType.INTEGER_TYPES) \
                    and self.num_kind(n.this, ctx) == "num" and not isinstance(n.this, exp.Anonymous):
                n.set("this", exp.Anonymous(this="TRUNC", expressions=[n.this.copy()]))
                fixes_v5._once(ctx, "trunc", "info", "CAST(decimal AS INT): T-SQL truncates but BigQuery rounds - "
                                                     "wrapped in TRUNC()")
        # CAST(x AS VARCHAR(n)): T-SQL silently truncates to n characters, BigQuery STRING(n) raises an error
        for n in list(tree.find_all(exp.Cast)):
            to = n.args.get("to")
            if isinstance(to, exp.DataType) and to.is_type(*exp.DataType.TEXT_TYPES) and to.expressions:
                p = to.expressions[0]
                p = p.this if isinstance(p, exp.DataTypeParam) else p
                if isinstance(p, exp.Literal) and str(p.this).isdigit():
                    src = n.this
                    if self.type_of(src, ctx) == "datetime":
                        ctx.warn("review", "CAST(datetime AS VARCHAR) uses the format 'Mon dd yyyy hh:miAM' in T-SQL "
                                           "but 'yyyy-mm-dd hh:mi:ss' in BigQuery; use FORMAT_DATETIME if the text "
                                           "format matters", n.sql("tsql"))
                    if self.num_kind(src, ctx) is not None or self.type_of(src, ctx) is not None or \
                            (isinstance(src, exp.Literal) and len(str(src.this)) <= int(p.this)):
                        continue                 # numbers/dates are not truncated (T-SQL returns '*' / errors)
                    inner = n.copy()
                    inner.set("to", exp.DataType.build("text"))
                    n.replace(exp.Substring(this=inner, start=exp.Literal.number(1), length=exp.Literal.number(p.this)))
        # STUFF(s, start, len, rep) -> SUBSTR(s, 1, start - 1) || rep || SUBSTR(s, start + len)
        for n in list(tree.find_all(exp.Stuff)):
            s_, st, ln, rp = n.this, n.args.get("start"), n.args.get("length"), n.expression
            if None in (s_, st, ln, rp):
                continue
            left = exp.Substring(this=s_.copy(), start=exp.Literal.number(1),
                                 length=exp.Sub(this=st.copy(), expression=exp.Literal.number(1)))
            right = exp.Substring(this=s_.copy(), start=exp.Add(this=st.copy(), expression=ln.copy()))
            n.replace(exp.paren(exp.DPipe(this=exp.DPipe(this=left, expression=rp.copy()), expression=right)))
            ctx.warn("info", "STUFF(s, start, len, rep) -> SUBSTR(s, 1, start-1) || rep || SUBSTR(s, start+len) "
                             "(T-SQL returns NULL when start is 0 or beyond the string; this version does not)")
        # LEN() ignores trailing spaces in T-SQL
        for n in list(tree.find_all(exp.Length)):
            if not isinstance(n.parent, exp.Length) and not isinstance(n.this, exp.Trim):
                n.set("this", exp.Trim(this=n.this.copy(), expression=exp.Literal.string(" "), position="TRAILING"))
        for n in list(tree.find_all(exp.NumberToStr)):
            ctx.warn("review", f"FORMAT(number, '{n.args.get('format').this if n.args.get('format') else ''}') uses .NET "
                               f"format strings; BigQuery needs FORMAT('%\\'.2f', x) style (NUMBER_TO_STR does not exist)",
                     n.sql("tsql"))
        # AVG(int) returns an INT in T-SQL (truncated); BigQuery AVG returns FLOAT64
        for n in list(tree.find_all(exp.Avg)):
            if self.num_kind(n.this, ctx) == "int":
                target = n.parent if isinstance(n.parent, exp.Window) else n
                target.replace(exp.cast(exp.Anonymous(this="TRUNC", expressions=[target.copy()]), "int64"))
                ctx.warn("info", "AVG of an integer expression: T-SQL returns a truncated INT, wrapped in "
                                 "CAST(TRUNC(AVG(...)) AS INT64)")
        # GROUP BY ... WITH ROLLUP / WITH CUBE -> GROUP BY ROLLUP(...) / CUBE(...)
        for g in list(tree.find_all(exp.Group)):
            for key, cls in (("rollup", exp.Rollup), ("cube", exp.Cube)):
                vals = g.args.get(key)
                if vals and all(isinstance(v, cls) and not v.expressions for v in vals) and g.expressions:
                    if key == "cube":
                        ctx.warn("review", "GROUP BY ... WITH CUBE: BigQuery supports GROUP BY CUBE(...) only in "
                                           "recent releases; verify", g.sql("tsql"))
                    g.set(key, [cls(expressions=[e.copy() for e in g.expressions])])
                    g.set("expressions", [])
        # COLLATE: BigQuery uses COLLATE(x, 'und:ci') with its own collation names
        for n in list(tree.find_all(exp.Collate)):
            name = n.expression.name.upper() if n.expression is not None else ""
            if "_CS" in name or name.endswith("_BIN") or name.endswith("_BIN2"):
                n.replace(n.this.copy())                          # BigQuery compares case-sensitively anyway
            elif "_CI" in name or name == "DATABASE_DEFAULT":
                n.replace(exp.Anonymous(this="COLLATE", expressions=[n.this.copy(), exp.Literal.string("und:ci")]))
                ctx.warn("info", f"COLLATE {name} -> COLLATE(x, 'und:ci') (case-insensitive)")
            else:
                ctx.warn("review", f"COLLATE {name} has no BigQuery equivalent", n.sql("tsql"))
        for n in list(tree.find_all(exp.Extract)):
            if n.this is not None and n.this.name.upper() == "WEEK":
                ctx.warn("info", "DATEPART(week, x): T-SQL week 1 is the week containing Jan 1; BigQuery EXTRACT(WEEK) "
                                 "starts week 1 on the first Sunday (days before are week 0) - results differ in "
                                 "early January")
        for fn in list(tree.find_all(exp.Anonymous)):
            nm = str(fn.name).upper()
            if nm == "DATALENGTH":
                fn.replace(exp.Anonymous(this="BYTE_LENGTH", expressions=[e.copy() for e in fn.expressions]))
                ctx.warn("info", "DATALENGTH -> BYTE_LENGTH (T-SQL counts 2 bytes per NVARCHAR character, BigQuery "
                                 "counts UTF-8 bytes)")
            elif nm == "ROWCOUNT_BIG":
                fn.replace(exp.column(ROWCOUNT_PH))
            elif nm == "XACT_STATE" and ctx.tran_flag:
                # in a CATCH block a failed transaction can only be rolled back in BigQuery: XACT_STATE() = -1
                state = -1 if ctx.catch_depth > 0 else 1
                fn.replace(exp.paren(exp.Case(ifs=[exp.If(this=exp.column(ctx.tran_flag),
                                                          true=exp.Literal.number(state) if state > 0 else
                                                          exp.Neg(this=exp.Literal.number(1)))],
                                              default=exp.Literal.number(0))))

    def fix_types(self, tree, ctx: Ctx):
        """Implicit conversions T-SQL does that BigQuery does not."""
        # CONCAT / CONCAT_WS: T-SQL converts every argument to text; BigQuery requires STRING arguments
        for n in list(tree.find_all(exp.Concat, exp.ConcatWs)):
            new = []
            for e in n.expressions:
                if isinstance(e, exp.Null) or self.is_stringy(e, ctx):
                    new.append(e)
                else:                                   # CAST(<string> AS STRING) is a harmless no-op
                    new.append(exp.cast(e.copy(), "text"))
            n.set("expressions", new)
        # ISNULL(int_expr, '') -> T-SQL converts '' to 0
        for n in list(tree.find_all(exp.Coalesce)):
            if not n.args.get("is_null") or len(n.expressions) != 1:
                continue
            a, b = n.this, n.expressions[0]
            if self.num_kind(a, ctx) is not None and isinstance(b, exp.Literal) and b.is_string:
                try:
                    v = str(b.this).strip() or "0"
                    float(v)
                    n.set("expressions", [exp.Literal.number(v)])
                except ValueError:
                    ctx.warn("review", f"ISNULL(number, '{b.this}'): T-SQL would fail converting the text to a number",
                             n.sql("tsql"))
            elif self.type_of(a, ctx) in ("date", "datetime") and isinstance(b, exp.Literal) and b.is_string \
                    and str(b.this).strip() == "":
                ctx.warn("review", "ISNULL(date, ''): T-SQL turns '' into 1900-01-01; BigQuery cannot mix DATE and "
                                   "STRING - decide what the NULL replacement should be", n.sql("tsql"))
            elif self.is_stringy(a, ctx) and isinstance(b, exp.Literal) and not b.is_string \
                    and not isinstance(a, exp.Literal):
                n.set("expressions", [exp.Literal.string(str(b.this))])
        # datetime + n / datetime - n : n days (legacy DATETIME arithmetic)
        for n in list(tree.find_all(exp.Add, exp.Sub)):
            ta, tb = self.type_of(n.this, ctx), self.type_of(n.expression, ctx)
            if ta in ("datetime", "date", "timestamp") and tb in ("datetime", "date", "timestamp"):
                ctx.warn("review", "subtracting/adding two dates: T-SQL returns a DATETIME offset from 1900-01-01; use "
                                   "DATETIME_DIFF(a, b, unit)", n.sql("tsql"))
            elif ta == "datetime" and self.num_kind(n.expression, ctx) == "int":
                cls = exp.DatetimeAdd if isinstance(n, exp.Add) else exp.DatetimeSub
                n.replace(cls(this=n.this.copy(), expression=n.expression.copy(), unit=exp.var("DAY")))
            elif ta == "datetime" and self.num_kind(n.expression, ctx) == "num":
                ctx.warn("review", "datetime + fractional number (fractions of a day) is not supported; use "
                                   "DATETIME_ADD(x, INTERVAL n MINUTE)", n.sql("tsql"))

    def check_unsupported(self, tree, ctx: Ctx):
        for fn in tree.find_all(exp.Anonymous):
            nm = str(fn.name).upper()
            if nm in TSQL_ONLY_FUNCS and not (nm in WATCHLIST):
                ctx.warn("review", f"{nm}() does not exist in BigQuery: {TSQL_ONLY_FUNCS[nm]}", fn.sql("tsql"))
        for d in tree.find_all(exp.DateAdd):
            if isinstance(d.this, exp.Literal) and not d.this.is_string:
                ctx.warn("review", "DATEADD(..., <number>): T-SQL treats the number as days since 1900-01-01; "
                                   "BigQuery needs a DATE/DATETIME value", d.sql("bigquery"))

    def postprocess(self, tree, ctx: Ctx):
        cfg = self.cfg
        fixes_v5.early(self, tree, ctx)
        self.fix_for_xml(tree, ctx)
        self.fix_datetime(tree, ctx)
        self.fix_functions(tree, ctx)
        self.fix_bits(tree, ctx)
        self.fix_integer_division(tree, ctx)
        self.fix_like(tree, ctx)
        self.fix_string_case(tree, ctx)
        self.fix_types(tree, ctx)
        # string concatenation: '+' -> '||'
        for node in list(tree.find_all(exp.Add)):
            if self.is_stringy(node, ctx):
                a, b = node.this, node.expression
                na, nb = self.num_kind(a, ctx), self.num_kind(b, ctx)
                if (na is not None) != (nb is not None):
                    # 'text' + int : int has higher precedence in T-SQL, so this is a NUMERIC addition there
                    s_side, n_kind = (b, na) if na is not None else (a, nb)
                    if not (isinstance(s_side, exp.Literal)):
                        ctx.warn("review", "string + number: T-SQL converts the string to a number here (numeric "
                                           "addition, not concatenation) - converted to CAST(... AS INT64/NUMERIC) + n",
                                 node.sql("tsql"))
                        cast = exp.cast(s_side.copy(), "int64" if n_kind == "int" else "numeric")
                        if s_side is a:
                            node.set("this", cast)
                        else:
                            node.set("expression", cast)
                        continue
                node.replace(exp.DPipe(this=node.this, expression=node.expression))
        # qualified columns: drop schema/database parts
        for col in tree.find_all(exp.Column):
            if col.args.get("db"):
                col.set("db", None)
                col.set("catalog", None)
        # table names -> `project.dataset.table`
        ctes = {c.alias.lower() for c in tree.find_all(exp.CTE) if c.alias}
        # scalar UDF calls: dbo.fn_x(a) -> `project.dataset.fn_x`(a)
        for d in list(tree.find_all(exp.Dot)):
            fn = d.expression
            if isinstance(fn, exp.Anonymous) and isinstance(d.this, exp.Identifier) \
                    and str(fn.name).lower() not in ("value", "query", "exist", "nodes", "modify"):
                full = f"{cfg.project}.{ctx.dataset}.{routine_name(cfg, d.this.name, str(fn.name))}"
                d.replace(exp.Anonymous(this=exp.to_identifier(full, quoted=True),
                                        expressions=[e.copy() for e in fn.expressions]))
                ctx.warn("review", f"call to user-defined function {d.this.name}.{fn.name}: this tool does not "
                                   f"convert functions - create `{full}` with CREATE FUNCTION first")
        for tbl in list(tree.find_all(exp.Table)):
            if isinstance(tbl.this, exp.Anonymous) and tbl.args.get("db") is not None:   # dbo.tvf(...)
                fn = tbl.this
                full = f"{cfg.project}.{ctx.dataset}.{routine_name(cfg, tbl.db, str(fn.name))}"
                tbl.set("this", exp.Anonymous(this=exp.to_identifier(full, quoted=True),
                                              expressions=[e.copy() for e in fn.expressions]))
                tbl.set("db", None)
                tbl.set("catalog", None)
                ctx.warn("review", f"table-valued function {fn.name}: convert it to a BigQuery TABLE FUNCTION "
                                   f"`{full}` (this tool does not convert functions)")
                continue
            if isinstance(tbl.this, exp.Dot) or tbl.args.get("catalog") is not None and \
                    isinstance(tbl.args.get("catalog"), exp.Dot):
                ctx.warn("review", f"four-part name {tbl.sql('tsql')}: linked servers are not reachable from "
                                   f"BigQuery (load the data or use a federated/external table)", tbl.sql("tsql"))
                continue
            if not isinstance(tbl.this, exp.Identifier):
                continue
            nm = tbl.name
            if nm.lower() in ctx.temp_names or (not tbl.args.get("db") and nm.lower() in ctes):
                continue
            if tbl.catalog and not cfg.has_dataset(tbl.catalog):
                ctx.warn("review", f"database '{tbl.catalog}' is not in config 'dataset_map': {tbl.sql('tsql')} was "
                                   f"pointed at the default dataset '{ctx.dataset}'")
            ds = cfg.dataset_for(tbl.catalog) if tbl.catalog else ctx.dataset
            full = f"{cfg.project}.{ds}.{object_name(cfg, tbl.db or None, nm)}"
            tbl.set("this", exp.to_identifier(full, quoted=True))
            tbl.set("db", None)
            tbl.set("catalog", None)
        # error functions
        for fn in list(tree.find_all(exp.Anonymous)):
            n = str(fn.name).upper()
            if n == "ERROR_MESSAGE":
                fn.replace(exp.column(ERRMSG_PH))
            elif n == "ERROR_LINE":
                fn.replace(exp.column(ERRLINE_PH))
                ctx.warn("info", "ERROR_LINE() -> @@error.stack_trace[SAFE_OFFSET(0)].line (a line number of the "
                                 "BigQuery script, not of the original T-SQL)")
            elif n in ("ERROR_NUMBER", "ERROR_SEVERITY", "ERROR_STATE"):
                fn.replace(exp.cast(exp.null(), "int64"))
                ctx.warn("info", f"{n}() has no BigQuery equivalent: returns NULL (the error text is in "
                                 f"@@error.message, location in @@error.stack_trace)")
            elif n == "ERROR_PROCEDURE":
                # @@error.stack_trace is ARRAY<STRUCT<line, column, filename, location>>, not a string, so it
                # cannot replace ERROR_PROCEDURE() (e.g. INSERT into a STRING column fails). Use the name instead.
                fn.replace(exp.Literal.string(ctx.proc_name) if ctx.proc_name else exp.cast(exp.null(), "string"))
                ctx.warn("info", f"ERROR_PROCEDURE() replaced by the procedure name '{ctx.proc_name}' (BigQuery has no "
                                 f"equivalent; if the error came from a called procedure, the call stack is in "
                                 f"@@error.stack_trace)")
        fixes_v5.late(self, tree, ctx)
        self.check_unsupported(tree, ctx)

    # ---------------------------------------------------------- statements -
    def emit_block(self, block: Block, ctx: Ctx) -> List[str]:
        out: List[str] = []
        for c in block.lead:
            out += norm_comment(c)
        plan = self.plan_cursors(block)
        consumed = set()
        stmts = block.stmts
        for pos, st in enumerate(stmts):
            if id(st) in consumed:
                continue
            action = plan.get(id(st))
            nxt = stmts[pos + 1] if pos + 1 < len(stmts) else None
            if action is None and nxt is not None and plan.get(id(nxt)) is None:
                err = self.at_error_pattern(st, nxt, ctx)
                if err is not None:
                    consumed.add(id(nxt))
                    out += err
                    continue
            if action is None:
                out += self.emit(st, ctx)
            elif action[0] == "skip":
                flat = ' '.join(st.text.split())
                out += [f"-- [cursor -> FOR loop] {flat if len(flat) <= 110 else flat[:107].rstrip() + ' ...'}"]
            else:
                ctx.pending = []
                lead: List[str] = []
                for c in getattr(st, "lead", []):
                    lead += norm_comment(c)
                out += lead + self.emit_cursor_loop(st, action[1], ctx)
        for c in block.tail:
            out += norm_comment(c)
        return out

    _ERR_NE = re.compile(r"^[\s(]*@@ERROR\s*(<>|!=|>)\s*0[\s)]*$", re.I)
    _ERR_EQ = re.compile(r"^[\s(]*@@ERROR\s*=\s*0[\s)]*$", re.I)

    def at_error_pattern(self, st, nxt, ctx: Ctx) -> Optional[List[str]]:
        """`stmt; IF @@ERROR <> 0 ...` / `stmt; SET @e = @@ERROR` -> BEGIN stmt EXCEPTION WHEN ERROR THEN ... END.
        BigQuery has no @@ERROR: a failing statement raises, so the check becomes an exception handler."""
        if not isinstance(st, Simple) or st.kind not in ("INSERT", "UPDATE", "DELETE", "MERGE", "SELECT", "EXEC",
                                                         "EXECUTE", "TRUNCATE"):
            return None
        if isinstance(nxt, If) and (self._ERR_NE.match(nxt.cond_text) or self._ERR_EQ.match(nxt.cond_text)):
            failed, ok = (nxt.then, nxt.els) if self._ERR_NE.match(nxt.cond_text) else (nxt.els, nxt.then)
            body = self.emit(st, ctx)
            if ok is not None:
                body += self.emit_body(ok, ctx)
            handler = self.emit_body(failed, ctx) if failed is not None else []
            ctx.warn("info", "IF @@ERROR ... after a statement converted to BEGIN ... EXCEPTION WHEN ERROR THEN ... END "
                             "(the error number is not available; @@error.message has the text)")
            return (["-- NOTE: IF @@ERROR check converted to an exception handler"] + ["BEGIN"]
                    + indent(self.ensure_stmt(body)) + ["EXCEPTION WHEN ERROR THEN"]
                    + indent(self.ensure_stmt(handler)) + ["END;"])
        if isinstance(nxt, Simple) and nxt.kind == "SET" and len(nxt.toks) == 4 and nxt.toks[1].kind == "var" \
                and nxt.toks[2].is_p("=") and nxt.toks[3].text.upper() == "@@ERROR":
            var = self.map_token(nxt.toks[1], ctx)
            ctx.assigned.add(nxt.toks[1].text.lstrip("@").lower())
            body = self.emit(st, ctx)
            ctx.warn("info", f"SET {nxt.toks[1].text} = @@ERROR converted to an exception handler: {var} is 0 on "
                             f"success and 1 (not the SQL Server error number) on failure")
            return (["-- NOTE: SET @var = @@ERROR converted to an exception handler"] + ["BEGIN"]
                    + indent(body + [f"SET {var} = 0;"]) + ["EXCEPTION WHEN ERROR THEN"]
                    + indent([f"SET {var} = 1;"]) + ["END;"])
        return None

    # -- cursors -------------------------------------------------------------
    @staticmethod
    def _cursor_name(tok: Token) -> str:
        return ident_value(tok).lower()

    def plan_cursors(self, block: Block) -> dict:
        """Find the classic DECLARE/OPEN/FETCH/WHILE @@FETCH_STATUS=0/CLOSE/DEALLOCATE pattern."""
        stmts = block.stmts
        plan: dict = {}
        for di, d in enumerate(stmts):
            if not (isinstance(d, Simple) and self.is_cursor_decl(d)):
                continue
            name = self._cursor_name(d.toks[1])
            toks = d.toks
            for_idx = next((k for k, x in enumerate(toks) if x.up == "FOR" and k > 2), None)
            if for_idx is None:
                continue
            sel = toks[for_idx + 1:]
            for k in range(len(sel) - 1, 0, -1):     # trailing FOR UPDATE / FOR READ ONLY
                if sel[k].up == "FOR" and sel[k + 1:k + 2] and sel[k + 1].up in ("UPDATE", "READ"):
                    sel = sel[:k]
                    break
            if not sel or sel[0].up != "SELECT":
                continue
            open_i = fetch_i = while_i = None
            tail_ids = []
            for k in range(di + 1, len(stmts)):
                st = stmts[k]
                if isinstance(st, Simple) and len(st.toks) > 1:
                    if st.kind == "OPEN" and self._cursor_name(st.toks[1]) == name and open_i is None:
                        open_i = k
                    elif st.kind == "FETCH" and open_i is not None and fetch_i is None \
                            and self._fetch_cursor(st) == name:
                        fetch_i = k
                    elif st.kind in ("CLOSE", "DEALLOCATE") and self._cursor_name(st.toks[1]) == name:
                        tail_ids.append(id(st))
                if isinstance(st, While) and fetch_i is not None and while_i is None and k > fetch_i \
                        and re.fullmatch(r"[\s(]*@@FETCH_STATUS\s*(=\s*0|<>\s*-1|!=\s*-1)[\s)]*", st.cond_text, re.I) \
                        and isinstance(st.body, Block) and st.body.stmts \
                        and isinstance(st.body.stmts[-1], Simple) and st.body.stmts[-1].kind == "FETCH" \
                        and self._fetch_cursor(st.body.stmts[-1]) == name:
                    while_i = k
            if None in (open_i, fetch_i, while_i):
                continue
            plan[id(d)] = ("skip",)
            plan[id(stmts[open_i])] = ("skip",)
            plan[id(stmts[fetch_i])] = ("skip",)
            for tid in tail_ids:
                plan[tid] = ("skip",)
            plan[id(stmts[while_i])] = ("for", {"decl": d, "sel": sel, "name": name, "fetch": stmts[fetch_i]})
        return plan

    @staticmethod
    def _fetch_cursor(st: Simple) -> Optional[str]:
        t = st.toks
        for k, x in enumerate(t):
            if x.up == "FROM" and k + 1 < len(t):
                return ident_value(t[k + 1]).lower()
        return None

    def emit_cursor_loop(self, node: While, info: dict, ctx: Ctx) -> List[str]:
        decl, sel, name, fetch = info["decl"], info["sel"], info["name"], info["fetch"]
        try:
            text = self.render(sel, decl.text, decl.toks[0].start, ctx)
            tree = self.parse_sql(text)
            first = tree
            while isinstance(first, exp.SetOperation):          # UNION / EXCEPT: column names come from the 1st SELECT
                first = first.this
            if not isinstance(first, exp.Select) or any(isinstance(e, exp.Star) or
                                                        (isinstance(e, exp.Column) and isinstance(e.this, exp.Star))
                                                        for e in first.expressions):
                raise ValueError("cursor query must be a SELECT with an explicit column list")
            names, seen = [], set()
            for i, e in enumerate(first.expressions):
                nm = e.alias_or_name
                if not nm or nm.lower() in seen:
                    nm = f"col{i + 1}"
                    first.expressions[i] = exp.alias_(e.this if isinstance(e, exp.Alias) else e, nm)
                seen.add(nm.lower())
                names.append(nm)
            # FETCH NEXT ... immediately followed by CONTINUE inside the loop body: the FOR loop advances by itself
            def mark(block):
                if not isinstance(block, Block):
                    return
                for a, b in zip(block.stmts, block.stmts[1:] + [None]):
                    if isinstance(a, Simple) and a.kind == "FETCH" and self._fetch_cursor(a) == name \
                            and isinstance(b, Simple) and b.kind == "CONTINUE":
                        ctx.skip_ids.add(id(a))
                for s in block.stmts:
                    for sub in ((s.then, s.els) if isinstance(s, If) else
                                (s.body,) if isinstance(s, While) else
                                (s.try_, s.catch) if isinstance(s, TryCatch) else (s,) if isinstance(s, Block) else ()):
                        mark(sub)
            mark(node.body)
            self.postprocess(tree, ctx)
            sel_sql = self.finish(tree.sql("bigquery", pretty=True)).splitlines()
            # INTO list of the FETCH statement
            ft = fetch.toks
            into = next((k for k, x in enumerate(ft) if x.up == "INTO"), None)
            if into is None:
                raise ValueError("FETCH without INTO")
            targets = [g for g in _split_commas(ft[into + 1:]) if g]
            if len(targets) != len(names) or any(len(g) != 1 or g[0].kind != "var" for g in targets):
                raise ValueError("FETCH ... INTO does not match the cursor's column list")
            rec = "rec_" + re.sub(r"\W", "_", name)
            sets = []
            for g, col in zip(targets, names):
                ctx.assigned.add(g[0].text.lstrip("@").lower())
                sets.append(f"SET {self.map_token(g[0], ctx)} = {rec}.{col};")
            head_warn = list(ctx.pending)
            body_block = Block(node.body.stmts[:-1], lead=node.body.lead, tail=node.body.tail)
            ctx.loop_depth += 1
            body = self.emit_block(body_block, ctx)
            ctx.loop_depth -= 1
            ctx.warn("info", f"cursor {name} converted to FOR loop (BigQuery has no cursors)")
            head_warn.append(ctx.warnings[-1])
            lines = [f"FOR {rec} IN ("] + indent(sel_sql) + [") DO"] + indent(sets + body) + ["END FOR;"]
            return self.attach_warnings(lines, head_warn)
        except Exception as e:  # noqa: BLE001
            ctx.warn("review", f"cursor loop could not be converted ({e})", node.cond_text)
            return self.attach_warnings(todo_block(f"cursor loop for {name} could not be converted ({e})",
                                                   decl.text + "\n" + fetch.text + "\nWHILE " + node.cond_text + " ..."),
                                        ctx.pending)

    def emit_body(self, node, ctx: Ctx) -> List[str]:
        return self.emit_block(node, ctx) if isinstance(node, Block) else self.emit(node, ctx)

    def emit(self, node, ctx: Ctx) -> List[str]:
        ctx.pending = []
        lead: List[str] = []
        for c in getattr(node, "lead", []):
            lead += norm_comment(c)
        if isinstance(node, Block):
            return lead + self.emit_block(node, ctx)
        if isinstance(node, If):
            lines = self.emit_if(node, ctx)
        elif isinstance(node, While):
            lines = self.emit_while(node, ctx)
        elif isinstance(node, TryCatch):
            lines = self.emit_try(node, ctx)
        else:
            lines = self.emit_simple(node, ctx)
        return lead + lines

    def attach_warnings(self, lines: List[str], pending: List[Warning_]) -> List[str]:
        notes = [f"-- TODO(review): {w.message}" if w.level == "review" else f"-- NOTE: {w.message}"
                 for w in pending]
        return notes + lines

    # -- compound ----------------------------------------------------------
    def cond_sql(self, toks, text, ctx: Ctx) -> str:
        rendered = self.render(toks, text, toks[0].start, ctx)
        try:
            return self.expr_sql(rendered, ctx)
        except Exception as e:  # noqa: BLE001
            ctx.warn("review", f"condition could not be converted ({e})", text)
            return f"TRUE /* TODO(manual): {' '.join(text.split())} */"

    def emit_if(self, node: If, ctx: Ctx) -> List[str]:
        special = self.guard_pattern(node)
        if special is not None:
            if special[1].startswith("IF OBJECT_ID") and any(t.kind == "temp" for t in special[0].toks):
                return [f"-- [removed] {' '.join(node.cond_text.split())} DROP ...: "
                        f"CREATE OR REPLACE TEMP TABLE makes the guard unnecessary"]
            if special[1].startswith("@@TRANCOUNT") and ctx.tran_flag:
                # keep the guard: ROLLBACK/COMMIT without an active transaction is an error in BigQuery too
                return [f"IF {ctx.tran_flag} THEN"] + indent(self.emit_simple(special[0], ctx)) + ["END IF;"]
            ctx.warn("info", special[1])
            return self.emit_simple(special[0], ctx)
        cond = self.cond_sql(node.cond_toks, node.cond_text, ctx)
        self.check_watchlist(Simple(node.cond_toks, node.cond_text, "IF"), ctx, cond)
        head = self.attach_warnings([f"IF {cond} THEN"], ctx.pending)
        d0 = ctx.tran_depth
        depths = []
        lines = head + self.branch(node.then, ctx)
        depths.append(ctx.tran_depth)
        els = node.els
        while els is not None:
            ctx.tran_depth = d0                 # every branch starts from the same transaction depth
            if isinstance(els, If) and self.guard_pattern(els) is None:
                for c in els.lead:
                    lines += norm_comment(c)
                ctx.pending = []
                c2 = self.cond_sql(els.cond_toks, els.cond_text, ctx)
                self.check_watchlist(Simple(els.cond_toks, els.cond_text, "IF"), ctx, c2)
                lines += self.attach_warnings([f"ELSEIF {c2} THEN"], ctx.pending)
                lines += self.branch(els.then, ctx)
                depths.append(ctx.tran_depth)
                els = els.els
            else:
                lines.append("ELSE")
                lines += self.branch(els, ctx)
                depths.append(ctx.tran_depth)
                break
        ctx.tran_depth = max(depths)
        lines.append("END IF;")
        return lines

    def branch(self, node, ctx: Ctx) -> List[str]:
        return indent(self.ensure_stmt(self.emit_body(node, ctx)))

    @staticmethod
    def ensure_stmt(lines: List[str]) -> List[str]:
        """BigQuery branches/blocks should hold at least one real statement."""
        if any(l.strip() and not l.lstrip().startswith("--") for l in lines):
            return lines
        return lines + ["ASSERT TRUE;  -- no-op placeholder: branch had no executable statement"]

    @staticmethod
    def guard_pattern(node: If):
        """Recognise idioms that BigQuery expresses differently.  Returns (Simple, note) or None."""
        if node.els is not None:
            return None
        then = node.then
        if isinstance(then, Block) and len(then.stmts) == 1:
            then = then.stmts[0]
        if not isinstance(then, Simple):
            return None
        cond = node.cond_text
        if (re.search(r"OBJECT_ID\s*\(", cond, re.I) and re.search(r"IS\s+NOT\s+NULL", cond, re.I)
                and then.kind == "DROP" and re.match(r"DROP\s+TABLE\b", then.text, re.I)):
            return then, "IF OBJECT_ID(...) IS NOT NULL DROP TABLE -> DROP TABLE IF EXISTS"
        if re.fullmatch(r"\s*\(?\s*(@@TRANCOUNT\s*(>\s*0|<>\s*0|!=\s*0|>=\s*1)|XACT_STATE\s*\(\s*\)\s*(<>|!=)\s*0)"
                        r"\s*\)?\s*", cond, re.I) and then.kind in ("ROLLBACK", "COMMIT"):
            return then, "@@TRANCOUNT guard removed (BigQuery transactions are not nestable)"
        return None

    def emit_while(self, node: While, ctx: Ctx) -> List[str]:
        cond = self.cond_sql(node.cond_toks, node.cond_text, ctx)
        self.check_watchlist(Simple(node.cond_toks, node.cond_text, "WHILE"), ctx, cond)
        head = self.attach_warnings([f"WHILE {cond} DO"], ctx.pending)
        ctx.loop_depth += 1
        body = self.ensure_stmt(self.emit_body(node.body, ctx))
        ctx.loop_depth -= 1
        return head + indent(body) + ["END WHILE;"]

    def emit_try(self, node: TryCatch, ctx: Ctx) -> List[str]:
        ctx.try_depth += 1
        lines = ["BEGIN"] + indent(self.ensure_stmt(self.emit_block(node.try_, ctx)))
        ctx.try_depth -= 1
        lines.append("EXCEPTION WHEN ERROR THEN")
        ctx.catch_depth += 1
        lines += indent(self.ensure_stmt(self.emit_block(node.catch, ctx)))
        ctx.catch_depth -= 1
        lines.append("END;")
        return lines

    # -- simple ------------------------------------------------------------
    def emit_simple(self, node: Simple, ctx: Ctx) -> List[str]:
        ctx.pending = [] if ctx.pending is None else ctx.pending
        kind = node.kind
        if id(node) in ctx.skip_ids:
            return [f"-- [cursor -> FOR loop] {' '.join(node.text.split())} (the FOR loop advances by itself)"]
        try:
            lines = self.dispatch(node, ctx)
        except Exception as e:  # noqa: BLE001
            ctx.warn("review", f"{kind} statement could not be converted ({e})", node.text)
            lines = todo_block(f"{kind} statement could not be converted automatically ({e})", node.text)
        self.check_watchlist(node, ctx, "\n".join(lines))
        first = next((l.strip().upper() for l in lines if l.strip() and not l.lstrip().startswith("--")), "")
        ctx.last_dml = first.startswith(("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE"))
        return self.attach_warnings(lines, ctx.pending)

    def dispatch(self, node: Simple, ctx: Ctx) -> List[str]:
        k, t = node.kind, node.toks
        if k == "DECLARE":
            return self.do_declare(node, ctx)
        if k == "SET":
            return self.do_set(node, ctx)
        if k in ("EXEC", "EXECUTE"):
            return self.do_exec(node, ctx)
        if k == "PRINT":
            expr = self.expr_sql(self.render_simple(node, ctx, t[1:]), ctx)
            if self.cfg.keep_print:
                return [f"SELECT {expr};"]
            return [f"-- PRINT removed: {expr}"]
        if k == "RETURN":
            if len(t) > 1 and ctx.return_param:
                val = self.expr_sql(self.render_simple(node, ctx, t[1:]), ctx)
                return [f"SET {ctx.return_param} = {val};", "RETURN;"]
            if len(t) > 1:
                ctx.warn("info", "RETURN value is discarded (BigQuery procedures return no status code; set config "
                                 "'return_status_mode': 'out_param' to keep it in an OUT parameter)", node.text)
            return ["RETURN;"]
        if k in ("BREAK", "CONTINUE"):
            return [f"{k};"]
        if k == "THROW":
            return self.do_throw(node, ctx)
        if k == "RAISERROR":
            return self.do_raiserror(node, ctx)
        flag_set = (lambda v: [f"SET {ctx.tran_flag} = {v};"] if ctx.tran_flag else [])
        if k == "BEGIN":
            if ctx.tran_depth >= 1:
                # T-SQL: a nested BEGIN TRAN only increments @@TRANCOUNT; BigQuery fails on a nested transaction
                ctx.tran_depth += 1
                ctx.warn("info", "nested BEGIN TRANSACTION removed: T-SQL only increments @@TRANCOUNT, BigQuery "
                                 "does not allow nested transactions (the matching inner COMMIT is removed too)")
                return [f"-- [removed] nested {' '.join(node.text.split())}"]
            ctx.tran_depth = 1
            ctx.in_tran = True
            return ["BEGIN TRANSACTION;"] + flag_set("TRUE")
        if k == "COMMIT":
            if ctx.tran_depth > 1:
                ctx.tran_depth -= 1
                return [f"-- [removed] inner {' '.join(node.text.split())} (T-SQL only decrements @@TRANCOUNT)"]
            ctx.tran_depth = 0
            ctx.in_tran = False
            return ["COMMIT TRANSACTION;"] + flag_set("FALSE")
        if k == "ROLLBACK":
            names = [ident_value(x).lower() for x in t[1:] if x.up not in ("TRAN", "TRANSACTION", "WORK")
                     and x.kind in ("word", "bracket", "var")]
            if names and names[0].lstrip("@") in ctx.savepoints:
                ctx.warn("review", "ROLLBACK to a savepoint: BigQuery has no savepoints - this would roll back the "
                                   "WHOLE transaction", node.text)
                return todo_block("rollback to savepoint is not supported", node.text)
            ctx.in_tran = False
            ctx.tran_depth = 0                  # T-SQL ROLLBACK (no savepoint) undoes all nesting levels too
            return ["ROLLBACK TRANSACTION;"] + flag_set("FALSE")
        if k == "SAVE":
            for x in t[1:]:
                if x.up not in ("TRAN", "TRANSACTION") and x.kind in ("word", "bracket", "var"):
                    ctx.savepoints.add(ident_value(x).lower().lstrip("@"))
                    break
            ctx.warn("review", "savepoints are not supported in BigQuery", node.text)
            return todo_block("savepoints are not supported", node.text)
        if len(t) == 2 and t[1].is_p(":"):
            ctx.warn("review", f"label {t[0].text}: BigQuery has no GOTO/labels; restructure with IF / LOOP / BREAK",
                     node.text)
            return todo_block("label (GOTO target) is not supported", node.text)
        if k in ("UPDATE", "DELETE") and len(t) > 1 and t[1].up == "TOP":
            ctx.warn("review", f"{k} TOP (n) has no BigQuery equivalent: rewrite as {k} ... WHERE key IN "
                               f"(SELECT key FROM ... ORDER BY ... LIMIT n)", node.text)
            return todo_block(f"{k} TOP (n) is not supported", node.text)
        if k == "INSERT" and any(x.up in ("EXEC", "EXECUTE") for x in t[2:6]):
            ctx.warn("review", "INSERT ... EXEC proc: BigQuery cannot insert a procedure's result set - let the called "
                               "procedure write into a TEMP TABLE (temp tables are visible to the caller in the same "
                               "script) or turn it into a TABLE FUNCTION and INSERT ... SELECT from it", node.text)
            return todo_block("INSERT ... EXEC is not supported", node.text)
        if k == "INSERT" and re.search(r"\bDEFAULT\s+VALUES\b", node.text, re.I):
            ctx.warn("review", "INSERT ... DEFAULT VALUES is not supported (BigQuery has no column defaults "
                               "unless the table defines them); list the columns explicitly", node.text)
            return todo_block("INSERT ... DEFAULT VALUES is not supported", node.text)
        if k == "USE":
            ctx.warn("info", "USE statement removed")
            return [f"-- [removed] {' '.join(node.text.split())}"]
        if k in ("OPEN", "CLOSE", "FETCH", "DEALLOCATE", "WAITFOR", "GOTO", "GRANT", "DENY", "REVOKE"):
            ctx.warn("review", f"{k} is not supported; rewrite manually", node.text)
            return todo_block(f"{k} is not supported in BigQuery", node.text)
        if k == "CREATE" and re.match(r"CREATE\s+(UNIQUE\s+)?(CLUSTERED\s+|NONCLUSTERED\s+)?(INDEX|STATISTICS)\b", node.text, re.I):
            ctx.warn("info", "index/statistics statement removed (not applicable in BigQuery)")
            return [f"-- [removed] {' '.join(node.text.split())}"]
        if k == "UPDATE" and re.match(r"UPDATE\s+STATISTICS\b", node.text, re.I):
            ctx.warn("info", "UPDATE STATISTICS removed")
            return [f"-- [removed] {' '.join(node.text.split())}"]
        if k == "CREATE" and re.match(r"CREATE\s+TABLE\b", node.text, re.I):
            return self.do_create_table(node, ctx)
        return self.do_sql(node, ctx)

    # DECLARE ---------------------------------------------------------------
    def do_declare(self, node: Simple, ctx: Ctx) -> List[str]:
        lines: List[str] = []
        for d in self.parse_declare(node):
            if d["cursor"]:
                ctx.warn("review", "cursors are not supported; rewrite as FOR record IN (SELECT ...) DO ... END FOR", node.text)
                return todo_block("cursor declaration", node.text)
            nm = ctx.vars[d["name"].lower()][0]
            if d["table"] is not None:
                lines += self.table_def_sql(nm, d["table"], node, ctx, temp=True)
                continue
            struct = self.table_type(d["type"]) if d["type"] else None
            if struct:                                  # variable of a user-defined table type
                kw = "CREATE TEMP TABLE" if ctx.in_tran else "CREATE OR REPLACE TEMP TABLE"
                cols = [c.strip() for c in struct.split(",")]
                lines += [f"{kw} {nm} ("] + indent([c + ("," if i < len(cols) - 1 else "") for i, c in enumerate(cols)]) \
                    + [");"]
                ctx.warn("info", f"@{d['name']} of table type {d['type'].strip()} -> TEMP TABLE {nm}")
                continue
            bq, note = map_type(d["type"])
            if note:
                ctx.warn("review", f"variable @{d['name']}: {note}")
            ctx.hoisted.setdefault(nm, bq)
            key = d["name"].lower()
            if d["init"] and key in ctx.dyn_vars:
                if len(d["init"]) == 1 and d["init"][0].kind == "string":
                    stmt, converted = self.dyn_string(d["init"], node, ctx)
                    (ctx.dyn_ok if converted else ctx.dyn_bad).add(key)
                    lines.append(f"SET {nm} = {stmt};")
                    continue
                ctx.dyn_bad.add(key)
            if d["init"]:
                rhs = self.expr_sql(self.render_simple(node, ctx, d["init"]), ctx)
                if bq == "BOOL" and rhs in ("0", "1"):
                    rhs = "TRUE" if rhs == "1" else "FALSE"
                lines.append(f"SET {nm} = {rhs};")
            # no initialiser: nothing to emit. T-SQL does NOT reset a variable declared inside a loop on each
            # iteration (DECLARE is not executable), so emitting `SET v = NULL` there would change the result.
        return lines

    # CREATE TABLE / table variables ----------------------------------------
    CONSTRAINT_WORDS = {"NOT", "NULL", "IDENTITY", "PRIMARY", "DEFAULT", "UNIQUE",
                        "CHECK", "COLLATE", "REFERENCES", "CONSTRAINT", "CLUSTERED",
                        "NONCLUSTERED", "FOREIGN", "SPARSE", "ROWGUIDCOL"}

    def table_def_sql(self, name: str, def_toks: List[Token], node: Simple, ctx: Ctx, temp: bool) -> List[str]:
        # def_toks: '(' ... ')'
        inner = def_toks[1:-1] if def_toks and def_toks[0].is_p("(") else def_toks
        cols = []
        for g in _split_commas(inner):
            if not g:
                continue
            first = g[0]
            if first.up in ("PRIMARY", "UNIQUE", "CONSTRAINT", "FOREIGN", "CHECK", "INDEX"):
                ctx.warn("info", "table-level constraint dropped (not enforced in BigQuery)", self.slice(node, g))
                continue
            cname = ident_value(first)
            j, tt, depth = 1, [], 0
            while j < len(g):
                tk = g[j]
                if tk.is_p("("):
                    depth += 1
                elif tk.is_p(")"):
                    depth -= 1
                if depth == 0 and tk.up in self.CONSTRAINT_WORDS:
                    break
                tt.append(tk)
                j += 1
            rest = [tk.up for tk in g[j:]]
            bq, note = map_type(self.slice(node, tt), parameterized=True)
            if note:
                ctx.warn("review", f"column {cname}: {note}")
            col = f"{cname} {bq}"
            if "NOT" in rest and "NULL" in rest:
                col += " NOT NULL"
            if "IDENTITY" in rest:
                ctx.warn("review", f"IDENTITY on column {cname} dropped; BigQuery has no identity columns")
            if "DEFAULT" in rest:
                ctx.warn("info", f"DEFAULT on column {cname} dropped")
            cols.append(col)
        # temp tables live until the whole script ends, so OR REPLACE keeps a procedure re-runnable -
        # except inside a transaction, where BigQuery only allows plain CREATE TEMP TABLE
        if temp and ctx.in_tran:
            ctx.warn("info", f"temp table {name} is created inside a transaction: BigQuery only allows CREATE TEMP TABLE "
                             f"there (not CREATE OR REPLACE), so calling this procedure twice in one script fails")
        if not temp and ctx.in_tran:
            ctx.warn("review", "permanent table DDL inside a transaction: BigQuery only allows DDL on TEMP tables between BEGIN TRANSACTION "
                               "and COMMIT - move it before BEGIN TRANSACTION", name)
        kw = ("CREATE TEMP TABLE" if ctx.in_tran else "CREATE OR REPLACE TEMP TABLE") if temp else "CREATE TABLE"
        return [f"{kw} {name} ("] + indent([c + ("," if i < len(cols) - 1 else "") for i, c in enumerate(cols)]) + [");"]

    def do_create_table(self, node: Simple, ctx: Ctx) -> List[str]:
        t = node.toks
        j = 2
        parts = []
        while j < len(t) and (t[j].kind in ("word", "bracket", "dquote", "temp")):
            parts.append(t[j])
            j += 1
            if j < len(t) and t[j].is_p("."):
                j += 1
            else:
                break
        if not parts or j >= len(t) or not t[j].is_p("("):
            raise ValueError("unsupported CREATE TABLE form")
        first = parts[-1]
        if first.kind == "temp":
            name = self.map_token(first, ctx)
            return self.table_def_sql(name, t[j:], node, ctx, temp=True)
        vals = [ident_value(p) for p in parts]
        db, schema, obj = qualify(vals)
        ds = self.cfg.dataset_for(db) if db else ctx.dataset
        name = f"`{self.cfg.project}.{ds}.{object_name(self.cfg, schema, obj)}`"
        return self.table_def_sql(name, t[j:], node, ctx, temp=False)

    # SET -------------------------------------------------------------------
    def do_set(self, node: Simple, ctx: Ctx) -> List[str]:
        t = node.toks
        if len(t) > 1 and t[1].up in SEMANTIC_SET_OPTIONS:
            opt = t[1].up
            val = ident_value(t[2]).upper().strip("'") if len(t) > 2 else ""
            harmless = ((opt in ("ANSI_NULLS", "CONCAT_NULL_YIELDS_NULL", "ANSI_WARNINGS") and val == "ON")
                        or (opt == "DATEFIRST" and val == "7")
                        or (opt == "LANGUAGE" and val in ("US_ENGLISH", "ENGLISH")))
            if not harmless:
                ctx.warn("review", f"SET {opt} removed, but it changes how the following code behaves "
                                   f"({SEMANTIC_SET_OPTIONS[opt]}); check the converted logic", node.text)
            return [f"-- [removed] {' '.join(node.text.split())}"]
        if len(t) > 1 and t[1].up == "IDENTITY_INSERT":
            ctx.warn("info", "SET IDENTITY_INSERT removed (BigQuery has no identity columns; explicit values are "
                             "always accepted)")
            return [f"-- [removed] {' '.join(node.text.split())}"]
        if len(t) > 1 and t[1].up in NOOP_SET_OPTIONS:
            return [f"-- [removed] {' '.join(node.text.split())}"]
        if any(x.up == "CURSOR" for x in t):
            ctx.warn("review", "cursor variables (SET @c = CURSOR FOR ...) are not supported; rewrite as "
                               "FOR record IN (SELECT ...) DO ... END FOR", node.text)
            return todo_block("cursor variable", node.text)
        if len(t) > 1 and t[1].up == "ROWCOUNT":
            ctx.warn("review", "SET ROWCOUNT is not supported; use LIMIT", node.text)
            return todo_block("SET ROWCOUNT", node.text)
        if len(t) > 3 and t[1].kind == "var":
            var = self.map_token(t[1], ctx)
            ctx.assigned.add(t[1].text.lstrip("@").lower())
            if t[2].is_p("="):
                rhs_toks = t[3:]
                op = None
            elif t[2].kind == "punct" and t[3].is_p("=") and t[2].text in "+-*/%":
                rhs_toks, op = t[4:], t[2].text
            else:
                raise ValueError("unsupported SET form")
            key = t[1].text.lstrip("@").lower()
            if key in ctx.dyn_vars:
                if op is None and len(rhs_toks) == 1 and rhs_toks[0].kind == "string":
                    stmt, converted = self.dyn_string(rhs_toks, node, ctx)
                    (ctx.dyn_ok if converted else ctx.dyn_bad).add(key)
                    return [f"SET {var} = {stmt};"]
                ctx.dyn_bad.add(key)
            rhs = self.render_simple(node, ctx, rhs_toks)
            if op:
                rhs = f"{var} {op} ({rhs})" if len(rhs_toks) > 1 else f"{var} {op} {rhs}"
            val = self.expr_sql(rhs, ctx)
            if ctx.vars.get(t[1].text.lstrip("@").lower(), ("", ""))[1] == "BOOL" and val in ("0", "1"):
                val = "TRUE" if val == "1" else "FALSE"
            return [f"SET {var} = {val};"]
        raise ValueError("unsupported SET form")

    # EXEC ------------------------------------------------------------------
    def do_exec(self, node: Simple, ctx: Ctx) -> List[str]:
        t = node.toks
        i = 1
        status_var = None
        if len(t) > 2 and t[i].kind == "var" and t[i + 1].is_p("="):
            status_var = t[i]
            ctx.assigned.add(t[i].text.lstrip("@").lower())
            i += 2
        if i >= len(t):
            raise ValueError("empty EXEC")
        # dynamic SQL: EXEC (@sql)
        if t[i].is_p("("):
            body = t[i + 1:-1]
            if len(body) == 1 and body[0].kind == "string":       # EXEC('stmt1; stmt2') -> one EXECUTE IMMEDIATE each
                try:
                    parts = self.dyn_parts(body[0].text, ctx)
                except Exception:  # noqa: BLE001
                    parts = []
                if len(parts) > 1 and all(p.split()[0].upper() not in ("DECLARE", "SET") for p in parts):
                    out = []
                    for ptxt in parts:
                        sql = "\n".join(self.sql_lines(ptxt, ctx)).rstrip().rstrip(";")
                        out.append(f'EXECUTE IMMEDIATE r"""{sql}""";')
                    ctx.warn("review", "dynamic SQL with several constant statements split into one EXECUTE IMMEDIATE "
                                       "per statement; check the result", node.text)
                    return out
            expr, converted = self.dyn_string(body, node, ctx)
            if converted:
                ctx.warn("review", "dynamic SQL with a constant string was converted; check the result", node.text)
            else:
                ctx.warn("review", "dynamic SQL: the string being executed is still T-SQL and must be converted too", node.text)
                self.injection_warning(body, ctx)
            return [f"EXECUTE IMMEDIATE {expr};"]
        parts = []
        while i < len(t) and t[i].kind in ("word", "bracket", "dquote"):
            parts.append(ident_value(t[i]))
            i += 1
            if i < len(t) and t[i].is_p("."):
                i += 1
            else:
                break
        if not parts:
            raise ValueError("cannot read procedure name")
        db, schema, obj = qualify(parts)
        if obj.lower() == "sp_executesql":
            args = _split_commas(t[i:])
            if not args:
                raise ValueError("sp_executesql without statement")
            stmt, converted = self.dyn_string(args[0], node, ctx)
            using = self.dyn_using(args[1:], node, ctx)
            if converted:
                ctx.warn("review", "sp_executesql converted to EXECUTE IMMEDIATE ... USING; check the result", node.text)
            else:
                ctx.warn("review", "sp_executesql: converted to EXECUTE IMMEDIATE; the SQL string is still T-SQL and "
                                   "parameters must be bound with USING", node.text)
                self.injection_warning(args[0], ctx)
            return [f"EXECUTE IMMEDIATE {stmt}" + (f" USING {using}" if using else "") + ";"]
        if obj.lower() == "sp_rename":
            out = self.sp_rename(_split_commas(t[i:]), node, ctx)
            if out is not None:
                return out
        if obj.lower().startswith("sp_") or obj.lower().startswith("xp_"):
            ctx.warn("review", f"system procedure {obj} has no BigQuery equivalent", node.text)
            return todo_block(f"system procedure {obj} is not available in BigQuery", node.text)

        if db and not self.cfg.has_dataset(db):
            ctx.warn("review", f"database '{db}' is not in config 'dataset_map': the call goes to the default dataset",
                     node.text)
        ds = self.cfg.dataset_for(db) if db else ctx.dataset
        callee = f"`{self.cfg.project}.{ds}.{routine_name(self.cfg, schema, obj)}`"
        positional, named = [], {}
        for g in _split_commas(t[i:]):
            if not g:
                continue
            had_out = g[-1].up in ("OUT", "OUTPUT")
            if had_out:
                g = g[:-1]
            if len(g) > 2 and g[0].kind == "var" and g[1].is_p("="):
                value = g[2:]
                if not (len(value) == 1 and value[0].up == "DEFAULT"):
                    named[g[0].text.lstrip("@").lower()] = value
            elif len(g) == 1 and g[0].up == "DEFAULT":
                positional.append(None)          # placeholder: use the callee's default
                value = []
            else:
                positional.append(g)
                value = g
            if had_out:
                for x in value:
                    if x.kind == "var":
                        ctx.assigned.add(x.text.lstrip("@").lower())
        # same database first (explicit db.schema.proc, else the caller's folder), then any unique match
        target = None
        for f in ([db] if db else []) + ([ctx.folder] if ctx.folder and not db else []):
            target = target or ctx.registry.get(registry_key(schema, obj, f))
        if target is None and not db:
            target = ctx.registry.get(registry_key(schema, obj))
        args_sql: List[str] = []
        pre: List[str] = []
        plist = [p for p in target.params if not p.readonly or self.table_type(p.type_text)] \
            if target is not None else []
        if target is not None and len(positional) <= len(plist):
            for idx, p in enumerate(plist):
                bq_type = map_type(p.type_text)[0]
                given = positional[idx] if idx < len(positional) else named.get(p.name.lower())
                if given is None and p.name.lower() in named:
                    given = named[p.name.lower()]
                if p.is_out:
                    if given is not None and len(given) == 1 and given[0].kind == "var":
                        args_sql.append(self.map_token(given[0], ctx))
                        continue
                    # BigQuery needs a variable for OUT/INOUT arguments: create one
                    hv = f"{self.cfg.var_prefix}out{ctx.out_counter}_{p.name}"
                    ctx.out_counter += 1
                    ctx.hoisted[hv] = bq_type
                    init = "NULL"
                    if given is not None:
                        init = self.expr_sql(self.render_simple(node, ctx, given), ctx)
                    pre.append(f"SET {hv} = {init};")
                    ctx.warn("info", f"OUTPUT argument @{p.name} of {obj} needs a variable in BigQuery: using {hv}")
                    args_sql.append(hv)
                    continue
                if p.readonly and given is not None and len(given) == 1 and given[0].kind == "var":
                    nm, typ = ctx.vars.get(given[0].text.lstrip("@").lower(), (None, None))
                    if typ == "TABLE":                 # table variable -> ARRAY<STRUCT> (same column order as the type)
                        args_sql.append(f"ARRAY(SELECT AS STRUCT * FROM {nm})")
                        ctx.warn("info", f"table variable {given[0].text} passed to TVP @{p.name} as "
                                         f"ARRAY(SELECT AS STRUCT * FROM {nm}): columns must be in the type's order")
                        continue
                    if typ == "TVP":
                        args_sql.append(nm[len("UNNEST("):-1])
                        continue
                if given is not None:
                    val = self.expr_sql(self.render_simple(node, ctx, given), ctx)
                elif p.default is not None:
                    val = self.default_sql(p.default, ctx)
                else:
                    ctx.warn("review", f"argument @{p.name} missing in call to {obj}")
                    val = "NULL"
                if bq_type == "BOOL" and val in ("0", "1"):
                    val = "TRUE" if val == "1" else "FALSE"
                args_sql.append(val)
        else:
            if named:
                ctx.warn("review", f"named arguments in call to {obj}: callee not found, check argument order", node.text)
            elif target is None:
                ctx.warn("info", f"{obj} is not among the converted procedures: arguments passed as written "
                                 f"(defaults of the callee are NOT filled in)")
            for g in positional:
                args_sql.append(self.expr_sql(self.render_simple(node, ctx, g), ctx) if g else "DEFAULT")
            for nm, g in named.items():
                args_sql.append(self.expr_sql(self.render_simple(node, ctx, g), ctx) + f"  /* {nm} */")
        post: List[str] = []
        callee_has_status = (target is not None and (self.cfg.return_status_mode or "drop").lower() == "out_param"
                             and self.returns_status(target))
        if callee_has_status and len(positional) <= len(plist):
            if status_var is not None:
                args_sql.append(self.map_token(status_var, ctx))
                status_var = None
            else:
                hv = f"{self.cfg.var_prefix}rc{ctx.out_counter}"
                ctx.out_counter += 1
                ctx.hoisted[hv] = "INT64"
                args_sql.append(hv)
        if status_var is not None:
            # EXEC @rc = proc: BigQuery procedures have no return status. A procedure that never RETURNs a value
            # returns 0 in T-SQL (errors raise in BigQuery), so 0 is faithful in that case.
            if self.returns_status(target):
                ctx.warn("review", f"EXEC {status_var.text} = {obj}: the callee's RETURN status does not exist in "
                                   f"BigQuery - {status_var.text} stays unchanged; pass the status in an OUT parameter",
                         node.text)
            else:
                post.append(f"SET {self.map_token(status_var, ctx)} = 0;  -- T-SQL return status of a successful call")
        return pre + [f"CALL {callee}({', '.join(args_sql)});"] + post

    def injection_warning(self, toks: List[Token], ctx: Ctx):
        """Dynamic SQL built from variables / parameters: SQL injection risk."""
        names = [x.text.lstrip("@").lower() for x in toks if x.kind == "var"]
        risky = [n for n in names if n in ctx.dyn_bad or n in ctx.params_in]
        if risky and "__once_injection" not in ctx.ci_hints:
            ctx.ci_hints.add("__once_injection")
            ctx.warn("review", "SECURITY: dynamic SQL is built by string concatenation (" + ", ".join("@" + r for r in risky)
                     + "). If any part comes from a parameter or table data this is an SQL injection risk: pass values "
                       "with EXECUTE IMMEDIATE ... USING, and allow-list identifiers (table / column names) instead "
                       "of concatenating them")

    def sp_rename(self, args, node: Simple, ctx: Ctx) -> Optional[List[str]]:
        """EXEC sp_rename 'schema.table.col', 'new', 'COLUMN' / sp_rename 'schema.table', 'new'."""
        vals = []
        for g in args:
            if len(g) == 3 and g[0].kind == "var" and g[1].is_p("="):
                g = g[2:]
            if len(g) != 1 or g[0].kind != "string":
                return None
            vals.append(g[0].text[1:] if g[0].text[0] in "Nn" else g[0].text)
        vals = [v[1:-1].replace("''", "'") for v in vals]
        if len(vals) < 2:
            return None
        kind = vals[2].upper() if len(vals) > 2 else "OBJECT"
        parts = [x.strip("[]") for x in vals[0].split(".")]
        if kind == "COLUMN" and len(parts) >= 2:
            db, schema, tbl = qualify(parts[:-1])
            ds = self.cfg.dataset_for(db) if db else ctx.dataset
            full = f"`{self.cfg.project}.{ds}.{object_name(self.cfg, schema, tbl)}`"
            ctx.warn("info", "sp_rename (COLUMN) -> ALTER TABLE ... RENAME COLUMN (DDL: not allowed inside a transaction)")
            return [f"ALTER TABLE {full} RENAME COLUMN {parts[-1]} TO {vals[1]};"]
        if kind == "OBJECT":
            db, schema, tbl = qualify(parts)
            ds = self.cfg.dataset_for(db) if db else ctx.dataset
            full = f"`{self.cfg.project}.{ds}.{object_name(self.cfg, schema, tbl)}`"
            new = object_name(self.cfg, schema, vals[1].split(".")[-1].strip("[]"))
            ctx.warn("review", "sp_rename (table) -> ALTER TABLE ... RENAME TO: views and procedures cannot be renamed "
                               "this way in BigQuery; check the object type", node.text)
            return [f"ALTER TABLE {full} RENAME TO {new};"]
        if kind == "INDEX":
            ctx.warn("info", "sp_rename of an index removed (BigQuery has no indexes)")
            return [f"-- [removed] {' '.join(node.text.split())}"]
        return None

    @staticmethod
    def returns_status(proc: Optional[ProcDef]) -> bool:
        """True when the procedure may RETURN a status value (RETURN 1, RETURN -1, RETURN @x, RETURN (..)),
        or when it is unknown."""
        if proc is None:
            return True
        toks = list(iter_tokens(proc.body))
        for k, tk in enumerate(toks[:-1]):
            nxt = toks[k + 1]
            if tk.up == "RETURN" and (nxt.kind in ("number", "var") or nxt.is_p("-") or nxt.is_p("(")):
                return True
        return False

    def dyn_parts(self, txt: str, ctx: Ctx) -> List[str]:
        """Statements of a constant dynamic-SQL string, with #temp tables renamed (variables inside the string
        belong to the dynamic batch / sp_executesql parameters and stay @name)."""
        inner = (txt[1:] if txt[0] in "Nn" else txt)[1:-1].replace("''", "'")
        toks, comments = tokenize(inner)
        if not toks:
            return []
        out, pos = [], 0
        for tk in toks:
            out.append(inner[pos:tk.start])
            out.append(self.map_token(tk, ctx) if tk.kind in ("temp", "money") or
                       (tk.kind == "string" and tk.text[0] in "Nn") else tk.text)
            pos = tk.end
        out.append(inner[pos:])
        mapped = "".join(out)
        t2, c2 = tokenize(mapped)
        block = Parser(mapped, t2, c2).parse_all()
        if not all(isinstance(st, Simple) for st in block.stmts):
            raise ValueError("control flow inside dynamic SQL")
        return [st.text for st in block.stmts]

    def dyn_string(self, toks: List[Token], node: Simple, ctx: Ctx) -> Tuple[str, bool]:
        """BigQuery expression for a dynamic-SQL string; constant strings are converted from T-SQL."""
        if len(toks) == 1 and toks[0].kind == "var":
            key = toks[0].text.lstrip("@").lower()
            if key in ctx.dyn_ok and key not in ctx.dyn_bad:
                return self.map_token(toks[0], ctx), True
        if len(toks) == 1 and toks[0].kind == "string":
            try:
                parts = self.dyn_parts(toks[0].text, ctx)
                if len(parts) > 1:
                    ctx.warn("review", f"dynamic SQL string holds {len(parts)} statements: BigQuery EXECUTE IMMEDIATE "
                                       f"runs exactly one statement - split it", node.text)
                elif len(parts) == 1 and parts[0].split()[0].upper() in ("DECLARE", "SET", "IF", "WHILE", "BEGIN"):
                    ctx.warn("review", "dynamic SQL string contains scripting (DECLARE/SET/IF): EXECUTE IMMEDIATE "
                                       "runs one SQL statement", node.text)
                elif parts:
                    lines = self.sql_lines(parts[0], ctx)
                    sql = "\n".join(lines).rstrip().rstrip(";")
                    if '"""' not in sql and not sql.endswith('"') and not any(l.lstrip().startswith("SET ") for l in lines):
                        return 'r"""' + sql + '"""', True
            except Exception:  # noqa: BLE001
                pass
        return self.expr_sql(self.render_simple(node, ctx, toks), ctx), False

    def dyn_using(self, args: List[List[Token]], node: Simple, ctx: Ctx) -> str:
        """sp_executesql @stmt, @params, @a = x, ...  ->  USING x AS a, ..."""
        if not args:
            return ""
        decl_names: List[str] = []
        if len(args[0]) == 1 and args[0][0].kind == "string":
            decl_names = re.findall(r"@(\w+)", args[0][0].text)
            rest = args[1:]
        else:
            rest = args
        out = []
        for idx, g in enumerate(rest):
            if g and g[-1].up in ("OUT", "OUTPUT"):
                ctx.warn("review", "sp_executesql OUTPUT parameter: use EXECUTE IMMEDIATE ... INTO", node.text)
                g = g[:-1]
            if len(g) > 2 and g[0].kind == "var" and g[1].is_p("="):
                name, val = g[0].text.lstrip("@"), g[2:]
            elif idx < len(decl_names):
                name, val = decl_names[idx], g
            else:
                continue
            out.append(f"{self.expr_sql(self.render_simple(node, ctx, val), ctx)} AS {name}")
        return ", ".join(out)

    def default_sql(self, text: str, ctx: Ctx) -> str:
        return self.expr_sql(text, ctx)

    # THROW / RAISERROR -------------------------------------------------------
    def do_throw(self, node: Simple, ctx: Ctx) -> List[str]:
        t = node.toks
        if len(t) == 1:
            if ctx.catch_depth == 0:
                ctx.warn("review", "THROW without arguments outside a CATCH block", node.text)
            return ["RAISE;"]
        args = _split_commas(t[1:])
        if len(args) < 2:
            raise ValueError("unsupported THROW form")
        msg = self.expr_sql(self.render_simple(node, ctx, args[1]), ctx)
        return [f"RAISE USING MESSAGE = {msg};"]

    def do_raiserror(self, node: Simple, ctx: Ctx) -> List[str]:
        t = node.toks[1:]
        # strip WITH options
        for idx, tk in enumerate(t):
            if tk.up == "WITH" and idx > 0:
                t = t[:idx]
                break
        if t and t[0].is_p("(") and t[-1].is_p(")"):
            t = t[1:-1]
        args = _split_commas(t)
        if len(args) < 3:
            raise ValueError("unsupported RAISERROR form")
        if len(args[0]) == 1 and args[0][0].kind == "number":       # RAISERROR(50001, 16, 1): sys.messages id
            ctx.warn("review", f"RAISERROR({args[0][0].text}, ...) uses a message number from sys.messages; "
                               f"put the real message text here", node.text)
            msg = f"'Error {args[0][0].text}'"
        else:
            msg = self.expr_sql(self.render_simple(node, ctx, args[0]), ctx)
        sev_tok = args[1][0] if len(args[1]) == 1 else None
        if ctx.try_depth == 0 and not (sev_tok is not None and sev_tok.kind == "number" and int(float(sev_tok.text)) < 11):
            ctx.warn("info", "RAISERROR outside TRY: T-SQL CONTINUES with the next statement, BigQuery RAISE stops the "
                             "procedure (usually what was intended - check if code follows that should still run)")
        if len(args) > 3:
            fmt_args = ", ".join(self.expr_sql(self.render_simple(node, ctx, a), ctx) for a in args[3:])
            msg = f"FORMAT({msg}, {fmt_args})"
        sev = args[1][0].text if len(args[1]) == 1 and args[1][0].kind == "number" else None
        if sev is not None and int(float(sev)) < 11:
            ctx.warn("info", "RAISERROR with severity <= 10 is informational; converted to a comment", node.text)
            return [f"-- RAISERROR (informational): {msg}"]
        return [f"RAISE USING MESSAGE = {msg};"]

    # general SQL ---------------------------------------------------------------
    def do_sql(self, node: Simple, ctx: Ctx) -> List[str]:
        return self.sql_lines(self.render_simple(node, ctx), ctx)

    def sql_lines(self, text: str, ctx: Ctx) -> List[str]:
        tree = self.parse_sql(text)
        pre: List[str] = []

        self.inline_ctes(tree, ctx)
        if isinstance(tree, exp.Select):
            res = self.select_assignment(tree, ctx)
            if res is not None:
                return res
        if isinstance(tree, exp.Update):
            self.fix_update(tree, ctx)
        elif isinstance(tree, exp.Delete):
            self.fix_delete(tree, ctx)
        elif isinstance(tree, exp.Drop) and str(tree.args.get("kind", "")).upper() == "TABLE":
            tree.set("exists", True)
            dropped = tree.this
            if ctx.in_tran and isinstance(dropped, exp.Table) and dropped.name.lower() not in ctx.temp_names:
                ctx.warn("review", "permanent table DDL inside a transaction: BigQuery only allows DDL on TEMP tables between BEGIN TRANSACTION "
                               "and COMMIT - move it before BEGIN TRANSACTION", text)
        self.postprocess(tree, ctx)

        if isinstance(tree, exp.Select) and tree.args.get("into") is not None:
            return self.select_into(tree, ctx)

        sql = self.finish(tree.sql("bigquery", pretty=True)).rstrip(";")
        sql = re.sub(r"^(TRUNCATE TABLE)[ \t]{2,}", r"\1 ", sql)
        return pre + (sql + ";").splitlines()

    def select_assignment(self, tree: exp.Select, ctx: Ctx) -> Optional[List[str]]:
        names = {v[0].lower() for v in ctx.vars.values()}
        projs = tree.expressions
        assign = [isinstance(p, exp.Alias) and p.alias.lower() in names for p in projs]
        if not any(assign):
            return None
        if not all(assign):
            raise ValueError("mixes variable assignment and data retrieval in one SELECT")
        targets = [p.alias for p in projs]
        for nm in targets:
            for key, (bq, _) in ctx.vars.items():
                if bq.lower() == nm.lower():
                    ctx.assigned.add(key)
        values = [p.this for p in projs]
        has_from = tree.args.get("from_") is not None
        if not has_from:
            out = []
            for tgt, val in zip(targets, values):
                v = exp.select(val)
                self.postprocess(v, ctx)
                out.append(f"SET {tgt} = {self.finish(v.expressions[0].sql('bigquery'))};")
            return out
        tree.set("expressions", values)
        tlow = {x.lower() for x in targets}
        if any(c.name.lower() in tlow and not c.table for v in values for c in v.find_all(exp.Column)):
            # SELECT @s = @s + col FROM t / SELECT @s = COALESCE(@s + ',', '') + col FROM t  (row-by-row accumulation)
            ctx.warn("review", "SELECT @v = @v + ... FROM ... accumulates over ALL rows in T-SQL (string list / running "
                               "total); the converted single-row SET does not. Use STRING_AGG(col, ',') / SUM(col)",
                     tree.sql("bigquery"))
        if not tree.args.get("limit"):
            is_agg = tree.find(exp.AggFunc) is not None
            single_row = is_agg and not tree.args.get("group")
            if not single_row:
                tree.set("limit", exp.Limit(expression=exp.Literal.number(1)))
                ctx.warn("info", "SELECT @var = ... FROM ... assigns the LAST row in T-SQL; BigQuery version uses LIMIT 1 "
                                 "(add ORDER BY if the row matters). If NO row matches, T-SQL keeps the old value but "
                                 "BigQuery sets the variable to NULL")
        if len(targets) > 1:
            tree.set("kind", "STRUCT")
        self.postprocess(tree, ctx)
        sub = self.finish(tree.sql("bigquery", pretty=True))
        sub_lines = sub.splitlines()
        lhs = targets[0] if len(targets) == 1 else "(" + ", ".join(targets) + ")"
        return [f"SET {lhs} = ("] + indent(sub_lines) + [");"]

    def select_into(self, tree: exp.Select, ctx: Ctx) -> List[str]:
        into = tree.args["into"]
        tbl = into.this
        is_temp = tbl.name.lower() in ctx.temp_names
        if not is_temp and ctx.in_tran:
            ctx.warn("review", "permanent table DDL inside a transaction: BigQuery only allows DDL on TEMP tables between BEGIN TRANSACTION "
                               "and COMMIT - move it before BEGIN TRANSACTION", tbl.sql("bigquery"))
        sql = self.finish(tree.sql("bigquery", pretty=True)).rstrip(";")
        if re.match(r"CREATE\s+(OR\s+REPLACE\s+)?TABLE", sql, re.I):
            sql = re.sub(r"^CREATE\s+(OR\s+REPLACE\s+)?TABLE", self.temp_kw(is_temp, ctx), sql, count=1, flags=re.I)
        else:  # fallback: build manually
            tree.set("into", None)
            body = self.finish(tree.sql("bigquery", pretty=True))
            sql = f"{self.temp_kw(is_temp, ctx)} {tbl.sql('bigquery')} AS\n{body}"
        return (sql + ";").splitlines()

    @staticmethod
    def temp_kw(is_temp: bool, ctx: Ctx) -> str:
        if not is_temp:
            return "CREATE OR REPLACE TABLE"
        return "CREATE TEMP TABLE" if ctx.in_tran else "CREATE OR REPLACE TEMP TABLE"

    # ------------------------------------------------- UPDATE / DELETE with joins --------
    @staticmethod
    def _sources(first):
        """[first table, joined table 1, ...] and the join nodes."""
        joins = list(first.args.get("joins") or [])
        return [first] + [j.this for j in joins], joins

    @staticmethod
    def _matches(src, name: str) -> bool:
        return isinstance(src, (exp.Table, exp.Subquery)) and (
            (src.alias_or_name or "").lower() == name or (isinstance(src, exp.Table) and src.name.lower() == name))

    @staticmethod
    def _inner(j) -> bool:
        return not j.args.get("side") and j.args.get("kind") in (None, "INNER", "CROSS")

    @staticmethod
    def _and(conds):
        conds = [c for c in conds if c is not None]
        if not conds:
            return None
        w = conds[0]
        for c in conds[1:]:
            w = exp.and_(w, c)
        return w

    @staticmethod
    def _from_list(others):
        """FROM a, b, c (cross joins; the ON conditions go to WHERE)."""
        base = others[0].copy()
        base.set("joins", [exp.Join(this=o.copy()) for o in others[1:]] or None)
        return base

    def fix_update(self, tree: exp.Update, ctx: Ctx):
        frm = tree.args.get("from_")
        target = tree.this
        if frm is not None and isinstance(frm.this, exp.Table) and isinstance(target, exp.Table):
            first = frm.this
            cands, joins = self._sources(first)
            tname = target.name.lower()
            idx = next((k for k, c in enumerate(cands) if self._matches(c, tname)), None)
            if idx is not None:
                real = cands[idx].copy()
                real.set("joins", None)
                if not isinstance(real, exp.Table):
                    raise ValueError("UPDATE target is a derived table / CTE; rewrite against the base table")
                alias = real.alias_or_name
                where = tree.args.get("where")
                if all(self._inner(j) for j in joins):
                    conds = [j.args.get("on").copy() for j in joins if j.args.get("on") is not None]
                    others = [c for k, c in enumerate(cands) if k != idx]
                    tree.set("this", real)
                    tree.set("from_", exp.From(this=self._from_list(others)) if others else None)
                    tree.set("where", exp.Where(this=self._and(conds + [where.this.copy() if where else None]))
                             if (conds or where) else None)
                    grouped = others and all(isinstance(o, exp.Subquery) and o.this.args.get("group") is not None
                                             for o in others)
                    if others and not grouped:
                        ctx.warn("info", "UPDATE ... FROM: BigQuery raises an error if a target row matches more than "
                                         "one source row (T-SQL silently uses one of them) - make sure the joined "
                                         "source is unique on the join key (aggregate it first if needed)")
                elif idx == 0 and len(joins) == 1 and str(joins[0].args.get("side") or "").upper() == "LEFT":
                    # UPDATE t SET c = s.x FROM T t LEFT JOIN S s ON ... : every target row is updated, NULL when no
                    # match -> correlated scalar subqueries
                    j = joins[0]
                    src = j.this.copy()
                    salias = src.alias_or_name.lower()
                    if where is not None and any((c.table or "").lower() == salias for c in where.find_all(exp.Column)):
                        raise ValueError("UPDATE ... LEFT JOIN with a WHERE on the joined table; rewrite manually")
                    for eq in tree.expressions:
                        val = eq.expression
                        if any((c.table or "").lower() == salias for c in val.find_all(exp.Column)):
                            sub = exp.select(val.copy()).from_(src.copy())
                            if j.args.get("on") is not None:
                                sub = sub.where(j.args["on"].copy())
                            eq.set("expression", exp.Subquery(this=sub))
                    tree.set("this", real)
                    tree.set("from_", None)
                    ctx.warn("info", "UPDATE ... LEFT JOIN converted to correlated subqueries (BigQuery fails if the "
                                     "joined table has several matching rows; T-SQL used one of them)")
                else:
                    raise ValueError("UPDATE ... FROM with RIGHT/FULL/multiple outer joins needs a manual rewrite")
                for eq in tree.expressions:              # BigQuery wants unqualified column names on the left of SET
                    col = eq.this if isinstance(eq, exp.EQ) else None
                    if isinstance(col, exp.Column) and (col.table or "").lower() == alias.lower():
                        col.set("table", None)
            elif target.args.get("db") is None and not any(self._matches(c, tname) for c in cands) \
                    and any(c.alias for c in cands if isinstance(c, exp.Table)) \
                    and len(tname) <= 3:
                ctx.warn("review", f"UPDATE target '{target.name}' is not in the FROM list - if it is meant to be an "
                                   f"alias, the converted statement updates the wrong table", target.name)
        # T-SQL quirk: UPDATE t SET @v = col  (variable assignment inside UPDATE)
        names = {v[0].lower() for v in ctx.vars.values()}
        for eq in tree.expressions:
            if isinstance(eq, exp.EQ) and isinstance(eq.this, exp.Column) and not eq.this.table \
                    and eq.this.name.lower() in names:
                raise ValueError("UPDATE ... SET @variable = ... (assigning a variable inside UPDATE) is not supported")
            if isinstance(eq, exp.EQ) and isinstance(eq.expression, exp.EQ):
                raise ValueError("UPDATE ... SET @v = col = expr (running-total update) is not supported")
        if not tree.args.get("where"):
            tree.set("where", exp.Where(this=exp.true()))

    def fix_delete(self, tree: exp.Delete, ctx: Ctx):
        tables = tree.args.get("tables")
        if tables and not tree.this:              # DELETE dbo.T WHERE ...
            tree.set("this", tables[0].copy())
            tree.set("tables", None)
            tables = None
        real = tree.this
        if tables and isinstance(real, exp.Table):
            # DELETE x FROM T t [JOIN ...]: x names (alias of) the table rows are deleted from
            tname = tables[0].name.lower()
            cands, joins = self._sources(real)
            idx = next((k for k, c in enumerate(cands) if self._matches(c, tname)), None)
            if idx is None:
                raise ValueError(f"DELETE {tables[0].name} FROM ...: '{tables[0].name}' is not in the FROM list")
            tgt = cands[idx].copy()
            tgt.set("joins", None)
            if not isinstance(tgt, exp.Table):
                raise ValueError("DELETE through a derived table / CTE is not supported in BigQuery")
            where = tree.args.get("where")
            tree.set("tables", None)
            if not joins:
                tree.set("this", tgt)
            elif all(self._inner(j) for j in joins):
                others = [c for k, c in enumerate(cands) if k != idx]
                conds = [j.args.get("on").copy() for j in joins if j.args.get("on") is not None]
                sub = exp.select("1").from_(self._from_list(others))
                w = self._and(conds + [where.this.copy() if where else None])
                if w is not None:
                    sub = sub.where(w)
                tree.set("this", tgt)
                tree.set("where", exp.Where(this=exp.Exists(this=sub)))
                return
            elif idx == 0 and len(joins) == 1 and str(joins[0].args.get("side") or "").upper() == "LEFT" \
                    and where is not None and isinstance(where.this, exp.Is) \
                    and isinstance(where.this.this, exp.Column) \
                    and (where.this.this.table or "").lower() == joins[0].this.alias_or_name.lower() \
                    and isinstance(where.this.expression, exp.Null):
                # anti-join idiom: DELETE t FROM T t LEFT JOIN S s ON ... WHERE s.key IS NULL -> NOT EXISTS
                j = joins[0]
                sub = exp.select("1").from_(j.this.copy())
                if j.args.get("on") is not None:
                    sub = sub.where(j.args["on"].copy())
                tree.set("this", tgt)
                tree.set("where", exp.Where(this=exp.Not(this=exp.Exists(this=sub))))
                ctx.warn("info", "DELETE ... LEFT JOIN ... WHERE x IS NULL converted to WHERE NOT EXISTS (...)")
                return
            else:
                raise ValueError("DELETE ... FROM with outer joins needs a manual rewrite for BigQuery")
        if not tree.args.get("where"):
            tree.set("where", exp.Where(this=exp.true()))
