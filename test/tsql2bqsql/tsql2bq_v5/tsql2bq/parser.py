"""Structural parser: file -> procedures -> statement tree.

sqlglot parses individual statements well but has little support for whole
T-SQL procedures (header, parameters, IF/WHILE/TRY blocks).  This module
handles that structure and leaves each *simple* statement to the translator.
"""
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .tokens import Token, code_spans, ident_value, tokenize

STARTERS = {
    "SELECT", "INSERT", "UPDATE", "DELETE", "MERGE", "DECLARE", "SET", "IF",
    "WHILE", "BEGIN", "RETURN", "EXEC", "EXECUTE", "PRINT", "TRUNCATE",
    "CREATE", "DROP", "ALTER", "WITH", "THROW", "RAISERROR", "BREAK",
    "CONTINUE", "COMMIT", "ROLLBACK", "USE", "OPEN", "CLOSE", "FETCH",
    "DEALLOCATE", "WAITFOR", "GOTO", "GRANT", "DENY", "REVOKE", "SAVE",
}


class ParseError(Exception):
    pass


# --------------------------------------------------------------------------
# Statement tree
# --------------------------------------------------------------------------
@dataclass
class Simple:
    toks: List[Token]
    text: str
    kind: str
    lead: List[str] = field(default_factory=list)


@dataclass
class Block:
    stmts: list
    lead: List[str] = field(default_factory=list)
    tail: List[str] = field(default_factory=list)


@dataclass
class If:
    cond_toks: List[Token]
    cond_text: str
    then: object
    els: Optional[object] = None
    lead: List[str] = field(default_factory=list)


@dataclass
class While:
    cond_toks: List[Token]
    cond_text: str
    body: object
    lead: List[str] = field(default_factory=list)


@dataclass
class TryCatch:
    try_: Block
    catch: Block
    lead: List[str] = field(default_factory=list)


@dataclass
class Param:
    name: str
    type_text: str
    default: Optional[str] = None
    is_out: bool = False
    readonly: bool = False


@dataclass
class ProcDef:
    schema: Optional[str]
    name: str
    params: List[Param]
    options: str
    body: Block
    batch_src: str
    ddl_kind: str = "CREATE"
    header_comments: List[str] = field(default_factory=list)


# --------------------------------------------------------------------------
# Batches
# --------------------------------------------------------------------------
_GO_RE = re.compile(r"^[ \t]*GO(?:[ \t]+\d+)?[ \t]*(?:--.*)?$", re.I | re.M)


def split_batches(src: str) -> List[str]:
    """Split on GO lines. A 'GO' line inside a string literal or block comment is NOT a separator."""
    spans = code_spans(src)

    def inside(pos: int) -> bool:
        return any(a <= pos < b for a, b in spans)

    parts, last = [], 0
    for m in _GO_RE.finditer(src):
        go_pos = m.start() + (len(m.group()) - len(m.group().lstrip()))
        if inside(go_pos):
            continue
        parts.append(src[last:m.start()])
        last = m.end()
    parts.append(src[last:])
    return [p for p in parts if p.strip()]


# --------------------------------------------------------------------------
# Statement parser
# --------------------------------------------------------------------------
class _ScanState:
    def __init__(self, kind):
        self.kind = kind
        self.seen_main = False
        self.n_select = 0
        self.seen_offset = False
        self.has_cursor = False


class Parser:
    def __init__(self, src: str, toks: List[Token], comments: List[Token]):
        self.src, self.t, self.comments = src, toks, comments
        self.i = 0
        self.cursor = toks[0].start if toks else 0

    # -- helpers ----------------------------------------------------------
    def cur(self) -> Optional[Token]:
        return self.t[self.i] if self.i < len(self.t) else None

    def kw(self, i: int) -> str:
        return self.t[i].up if 0 <= i < len(self.t) else ""

    def lead_before(self, tok: Token) -> List[str]:
        out = [c.text for c in self.comments if self.cursor <= c.start < tok.start]
        return out

    def skip_semis(self):
        while self.cur() is not None and self.cur().is_p(";"):
            self.cursor = self.cur().end
            self.i += 1

    def text_of(self, toks: List[Token]) -> str:
        return self.src[toks[0].start:toks[-1].end] if toks else ""

    # -- public -------------------------------------------------------------
    def parse_all(self) -> Block:
        stmts = self.parse_list()
        if self.cur() is not None:
            raise ParseError(f"unexpected '{self.cur().text}' near offset {self.cur().start}")
        tail = [c.text for c in self.comments if c.start >= self.cursor]
        return Block(stmts, tail=tail)

    def parse_list(self) -> list:
        out = []
        while True:
            self.skip_semis()
            tok = self.cur()
            if tok is None or tok.up == "END":
                return out
            out.append(self.parse_statement())

    def parse_statement(self):
        tok = self.cur()
        lead = self.lead_before(tok)
        up = tok.up
        if up == "BEGIN":
            nxt = self.kw(self.i + 1)
            if nxt == "TRY":
                return self.parse_try(lead)
            if nxt in ("TRAN", "TRANSACTION", "DISTRIBUTED"):
                return self.parse_simple(lead)
            return self.parse_block(lead)
        if up in ("IF", "WHILE"):
            return self.parse_if_while(lead, up)
        if up == "ELSE":
            raise ParseError(f"unexpected ELSE near offset {tok.start}")
        return self.parse_simple(lead)

    # -- compound statements -------------------------------------------------
    def _consume(self, word: str):
        tok = self.cur()
        if tok is None or tok.up != word:
            raise ParseError(f"expected {word} near offset {tok.start if tok else 'EOF'}")
        self.cursor = tok.end
        self.i += 1

    def parse_block(self, lead):
        self._consume("BEGIN")
        stmts = self.parse_list()
        tail = [c.text for c in self.comments if self.cursor <= c.start < (self.cur().start if self.cur() else 10**12)]
        self._consume("END")
        return Block(stmts, lead=lead, tail=tail)

    def parse_try(self, lead):
        self._consume("BEGIN"); self._consume("TRY")
        t_stmts = self.parse_list()
        self._consume("END"); self._consume("TRY")
        self.skip_semis()
        self._consume("BEGIN"); self._consume("CATCH")
        c_stmts = self.parse_list()
        self._consume("END"); self._consume("CATCH")
        return TryCatch(Block(t_stmts), Block(c_stmts), lead=lead)

    def parse_if_while(self, lead, word):
        self._consume(word)
        start = self.i
        end = self.scan_cond(start)
        if end == start:
            raise ParseError(f"empty {word} condition")
        cond = self.t[start:end]
        self.i = end
        self.cursor = cond[-1].end
        self.skip_semis()
        body = self.parse_statement()
        if word == "WHILE":
            return While(cond, self.text_of(cond), body, lead=lead)
        self.skip_semis()
        els = None
        if self.cur() is not None and self.cur().up == "ELSE":
            self._consume("ELSE")
            els = self.parse_statement()
        return If(cond, self.text_of(cond), body, els, lead=lead)

    def scan_cond(self, start) -> int:
        i, depth, case, n = start, 0, 0, len(self.t)
        while i < n:
            tok = self.t[i]
            if tok.kind == "punct":
                if tok.text == "(":
                    depth += 1
                elif tok.text == ")":
                    depth -= 1
            elif tok.kind == "word":
                up = tok.up
                if up == "CASE":
                    case += 1
                elif up == "END":
                    if case > 0:
                        case -= 1
                    elif i > start:
                        break
                elif depth <= 0 and case == 0 and i > start and up in STARTERS and up != "WITH":
                    break
            i += 1
        return i

    # -- simple statements ---------------------------------------------------
    def parse_simple(self, lead):
        start = self.i
        first = self.t[start]
        if first.up == "END":
            raise ParseError(f"unexpected END near offset {first.start}")
        kind = first.up if first.kind == "word" else ""
        end = self.scan_simple(start, kind)
        toks = self.t[start:end]
        self.i = end
        self.cursor = toks[-1].end
        self.skip_semis()
        return Simple(toks, self.text_of(toks), kind, lead)

    def is_cte_start(self, i) -> bool:
        a = self.t[i + 1] if i + 1 < len(self.t) else None
        b = self.t[i + 2] if i + 2 < len(self.t) else None
        if a is None or a.kind not in ("word", "bracket", "dquote") or b is None:
            return False
        return b.up == "AS" or b.is_p("(")

    def scan_simple(self, start, kind) -> int:
        st = _ScanState(kind)
        t, n = self.t, len(self.t)
        i, depth, case = start, 0, 0
        while i < n:
            tok = t[i]
            if tok.kind == "punct":
                if tok.text == "(":
                    depth += 1
                elif tok.text == ")":
                    depth -= 1
                elif tok.text == ";" and depth <= 0:
                    break
            elif tok.kind == "word":
                up = tok.up
                if up == "CASE":
                    case += 1
                elif up == "END":
                    if case > 0:
                        case -= 1
                    elif depth <= 0 and i > start:
                        break
                elif depth <= 0 and case == 0 and i > start:
                    if up == "ELSE":
                        break
                    if up == "OFFSET":
                        st.seen_offset = True
                    if up == "CURSOR":
                        st.has_cursor = True
                    if up in STARTERS and self.is_new(st, i):
                        break
            i += 1
        return i

    def is_new(self, st: _ScanState, i: int) -> bool:
        up, prev, kind = self.t[i].up, self.kw(i - 1), st.kind
        if up in ("SELECT", "INSERT", "UPDATE", "DELETE", "MERGE"):
            if up == "SELECT" and prev in ("UNION", "ALL", "INTERSECT", "EXCEPT"):
                return False
            if kind == "MERGE":
                return False
            if kind == "WITH" and not st.seen_main:
                st.seen_main = True
                st.kind = up
                return False
            if kind == "INSERT" and up == "SELECT" and st.n_select == 0:
                st.n_select += 1
                return False
            if kind in ("DECLARE", "SET") and st.has_cursor and up in ("SELECT", "UPDATE"):
                return False
            return True
        if up == "SET":
            return kind not in ("UPDATE", "MERGE")
        if up == "WITH":
            return self.is_cte_start(i)
        if up in ("EXEC", "EXECUTE"):
            return kind != "INSERT"
        if up == "FETCH":
            return not st.seen_offset
        if up == "DROP":
            return kind != "ALTER"
        if up == "IF" and kind in ("DROP", "ALTER") and self.kw(i + 1) == "EXISTS":
            return False                        # DROP TABLE IF EXISTS x  (SQL Server 2016+)
        return True


# --------------------------------------------------------------------------
# Procedure headers
# --------------------------------------------------------------------------
def _find_create_proc(t: List[Token]):
    for i, tok in enumerate(t):
        if tok.up in ("CREATE", "ALTER"):
            j = i + 1
            if j >= len(t):
                break
            if t[j].up == "OR" and j + 1 < len(t) and t[j + 1].up == "ALTER":
                j += 2
            if j < len(t) and t[j].up in ("PROC", "PROCEDURE"):
                return i, j + 1, tok.up
    return None


def _split_commas(toks: List[Token]) -> List[List[Token]]:
    groups, cur, depth = [], [], 0
    for tok in toks:
        if tok.is_p("("):
            depth += 1
        elif tok.is_p(")"):
            depth -= 1
        if tok.is_p(",") and depth == 0:
            groups.append(cur)
            cur = []
        else:
            cur.append(tok)
    if cur:
        groups.append(cur)
    return groups


def _parse_params(src: str, toks: List[Token]) -> List[Param]:
    params = []
    for g in _split_commas(toks):
        if not g or g[0].kind != "var":
            continue
        name = g[0].text.lstrip("@")
        j = 1
        if j < len(g) and g[j].up == "AS":
            j += 1
        type_toks, depth = [], 0
        while j < len(g):
            tk = g[j]
            if tk.is_p("("):
                depth += 1
            elif tk.is_p(")"):
                depth -= 1
            if depth == 0 and (tk.is_p("=") or tk.up in ("OUT", "OUTPUT", "READONLY")):
                break
            type_toks.append(tk)
            j += 1
        default_toks = []
        if j < len(g) and g[j].is_p("="):
            j += 1
            while j < len(g) and g[j].up not in ("OUT", "OUTPUT", "READONLY"):
                default_toks.append(g[j])
                j += 1
        flags = {tk.up for tk in g[j:]}
        params.append(Param(
            name=name,
            type_text=src[type_toks[0].start:type_toks[-1].end] if type_toks else "",
            default=src[default_toks[0].start:default_toks[-1].end] if default_toks else None,
            is_out=bool(flags & {"OUT", "OUTPUT"}),
            readonly="READONLY" in flags,
        ))
    return params


def parse_batch(batch: str) -> Optional[ProcDef]:
    """Parse one batch.  Returns None when it does not hold a procedure."""
    t, comments = tokenize(batch)
    found = _find_create_proc(t)
    if not found:
        return None
    ci, k, ddl = found
    header_comments = [c.text for c in comments if c.start < t[ci].start]

    parts = []
    while k < len(t) and t[k].kind in ("word", "bracket", "dquote"):
        parts.append(ident_value(t[k]))
        k += 1
        if k < len(t) and t[k].is_p("."):
            k += 1
        else:
            break
    if not parts:
        raise ParseError("could not read procedure name")
    if k + 1 < len(t) and t[k].is_p(";") and t[k + 1].kind == "number":
        k += 2

    depth, as_idx = 0, None
    for idx in range(k, len(t)):
        tok = t[idx]
        if tok.is_p("("):
            depth += 1
        elif tok.is_p(")"):
            depth -= 1
        elif depth == 0 and tok.up == "AS":
            prev = t[idx - 1]
            if prev.up == "EXECUTE" or prev.kind == "var":
                continue
            as_idx = idx
            break
    if as_idx is None:
        raise ParseError("no AS keyword found after procedure header")

    hdr = t[k:as_idx]
    if hdr and hdr[0].is_p("("):
        d, m = 0, None
        for idx, tok in enumerate(hdr):
            if tok.is_p("("):
                d += 1
            elif tok.is_p(")"):
                d -= 1
                if d == 0:
                    m = idx
                    break
        params_toks, opt_toks = hdr[1:m], hdr[m + 1:]
    else:
        d, w = 0, len(hdr)
        for idx, tok in enumerate(hdr):
            if tok.is_p("("):
                d += 1
            elif tok.is_p(")"):
                d -= 1
            elif d == 0 and tok.up in ("WITH", "FOR"):
                w = idx
                break
        params_toks, opt_toks = hdr[:w], hdr[w:]

    params = _parse_params(batch, params_toks)
    options = batch[opt_toks[0].start:opt_toks[-1].end] if opt_toks else ""

    body_toks = t[as_idx + 1:]
    if not body_toks:
        body = Block([])
    else:
        parser = Parser(batch, body_toks, [c for c in comments if c.start > t[as_idx].end])
        parser.cursor = t[as_idx].end
        body = parser.parse_all()

    schema = parts[-2] if len(parts) >= 2 else None
    return ProcDef(schema, parts[-1], params, options, body, batch, ddl, header_comments)
