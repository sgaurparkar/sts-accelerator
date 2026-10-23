"""CREATE VIEW support: parse a view definition and hand the query to the translator."""
from dataclasses import dataclass, field
from typing import List, Optional

from .parser import ParseError
from .tokens import Token, ident_value, tokenize


@dataclass
class ViewDef:
    schema: Optional[str]
    name: str
    columns: List[str]
    options: str
    query_toks: List[Token]
    batch_src: str
    header_comments: List[str] = field(default_factory=list)


def _find_create_view(t: List[Token]):
    for i, tok in enumerate(t):
        if tok.up in ("CREATE", "ALTER"):
            j = i + 1
            if j + 1 < len(t) and t[j].up == "OR" and t[j + 1].up == "ALTER":
                j += 2
            if j < len(t) and t[j].up == "VIEW":
                return i, j + 1
    return None


def parse_view_batch(batch: str) -> Optional[ViewDef]:
    """Return a ViewDef when *batch* holds CREATE/ALTER VIEW, else None."""
    t, comments = tokenize(batch)
    found = _find_create_view(t)
    if not found:
        return None
    ci, k = found
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
        raise ParseError("could not read view name")
    columns: List[str] = []
    if k < len(t) and t[k].is_p("("):
        depth, j = 0, k
        while j < len(t):
            if t[j].is_p("("):
                depth += 1
            elif t[j].is_p(")"):
                depth -= 1
                if depth == 0:
                    break
            j += 1
        columns = [ident_value(x) for x in t[k + 1:j] if x.kind in ("word", "bracket", "dquote")]
        k = j + 1
    as_idx = next((i for i in range(k, len(t)) if t[i].up == "AS"), None)
    if as_idx is None:
        raise ParseError("no AS keyword found in view definition")
    options = batch[t[k].start:t[as_idx - 1].end] if as_idx > k else ""
    q = t[as_idx + 1:]
    while q and q[-1].is_p(";"):
        q = q[:-1]
    # trailing WITH CHECK OPTION
    if len(q) >= 3 and q[-1].up == "OPTION" and q[-2].up == "CHECK" and q[-3].up == "WITH":
        q = q[:-3]
    if not q:
        raise ParseError("empty view body")
    schema = parts[-2] if len(parts) >= 2 else None
    return ViewDef(schema, parts[-1], columns, options, q, batch, header_comments)
