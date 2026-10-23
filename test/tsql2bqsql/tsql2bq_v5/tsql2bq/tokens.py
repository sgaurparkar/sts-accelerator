"""Minimal T-SQL tokenizer (strings, comments, identifiers, variables)."""
import re
from dataclasses import dataclass

_TOKEN_RE = re.compile(
    r"""
    (?P<ws>\s+)
  | (?P<comment>--[^\n]*|/\*)
  | (?P<string>[Nn]?'(?:[^']|'')*')
  | (?P<bracket>\[(?:[^\]]|\]\])*\])
  | (?P<dquote>"(?:[^"]|"")*")
  | (?P<var>@@?[A-Za-z_\#$][\w#$@]*)
  | (?P<temp>\#\#?[A-Za-z_][\w#$]*)
  | (?P<money>\$[ \t]*[+-]?(?:\d+(?:\.\d*)?|\.\d+))
  | (?P<number>\d+(?:\.\d*)?(?:[eE][+-]?\d+)?|\.\d+)
  | (?P<word>[^\W\d]\w*)
  | (?P<punct>.)
    """,
    re.X | re.S,
)


@dataclass
class Token:
    kind: str      # comment|string|bracket|dquote|var|temp|money|number|word|punct
    text: str
    start: int
    end: int

    @property
    def up(self) -> str:
        return self.text.upper() if self.kind == "word" else ""

    def is_p(self, ch: str) -> bool:
        return self.kind == "punct" and self.text == ch


def _block_comment_end(src: str, pos: int) -> int:
    """End offset of the block comment starting at *pos* ('/*'). T-SQL block comments NEST:
    /* a /* b */ c */ is one comment. An unterminated comment runs to the end of the text."""
    depth, i, n = 0, pos, len(src)
    while i < n:
        two = src[i:i + 2]
        if two == "/*":
            depth += 1
            i += 2
        elif two == "*/":
            depth -= 1
            i += 2
            if depth == 0:
                return i
        else:
            i += 1
    return n


def tokenize(src: str):
    """Return (significant_tokens, comment_tokens). Whitespace is dropped."""
    sig, comments = [], []
    pos, n = 0, len(src)
    while pos < n:
        m = _TOKEN_RE.match(src, pos)
        kind = m.lastgroup
        end = m.end()
        if kind == "comment" and m.group() == "/*":
            end = _block_comment_end(src, pos)
        if kind != "ws":
            tok = Token(kind, src[pos:end], pos, end)
            (comments if kind == "comment" else sig).append(tok)
        pos = end
    return sig, comments


def code_spans(src: str):
    """(start, end) offsets of string literals and comments - text in which GO / keywords do not count."""
    spans = []
    pos, n = 0, len(src)
    while pos < n:
        m = _TOKEN_RE.match(src, pos)
        kind, end = m.lastgroup, m.end()
        if kind == "comment" and m.group() == "/*":
            end = _block_comment_end(src, pos)
        if kind in ("comment", "string", "bracket", "dquote"):
            spans.append((pos, end))
        pos = end
    return spans


def ident_value(tok: Token) -> str:
    """Unquoted value of an identifier token."""
    t = tok.text
    if tok.kind == "bracket":
        return t[1:-1].replace("]]", "]")
    if tok.kind == "dquote":
        return t[1:-1].replace('""', '"')
    return t
