"""
table_selector.py — which tables of which databases a run touches.

Table patterns (case-insensitive, shell wildcards * ? [..] allowed):

    Orders                                  table Orders in any schema of every database in scope
    dbo.Orders                              schema.table
    dbo.*          sales.Order*             wildcards
    [dbo].[Order Details]                   brackets for names with spaces or dots
    SalesDB/dbo.Orders                      only in database SalesDB
    sqlserver1/SalesDB/dbo.Orders           only on that server + database
    ResourceGroup1/sqlserver1/SalesDB/*     resource group / server / database / table

Patterns come from --tables (several values or comma-separated), --tables-file
(one per line, # comments) or a run spec. --exclude-tables uses the same syntax.
"""
import fnmatch
import re
from dataclasses import dataclass

_WILDCARD = re.compile(r"[*?\[]")
_PART = re.compile(r"\[((?:[^\]]|\]\])+)\]|([^.\[\]]+)")


class TablePatternError(ValueError):
    pass


@dataclass(frozen=True)
class TablePattern:
    raw: str
    table: str
    schema: str | None = None
    database: str | None = None
    server: str | None = None
    resource_group: str | None = None

    @property
    def is_exact(self) -> bool:
        """No wildcard in the table part — a pattern that matches nothing is reported."""
        return not _WILDCARD.search(self.table) and not (self.schema and _WILDCARD.search(self.schema))

    def matches_database(self, resource_group: str, server: str, database: str) -> bool:
        return all(_match(value, pat) for value, pat in (
            (resource_group, self.resource_group), (server, self.server), (database, self.database)))

    def matches_table(self, schema: str, table: str) -> bool:
        return _match(schema, self.schema) and _match(table, self.table)


def _match(value: str, pattern: str | None) -> bool:
    if pattern is None:
        return True
    return fnmatch.fnmatchcase(str(value).lower(), pattern.lower())


def _split_object(text: str) -> list[str]:
    """'dbo.Orders' -> [dbo, Orders]; '[a.b].[c]' -> [a.b, c]."""
    parts, pos = [], 0
    text = text.strip()
    while pos < len(text):
        m = _PART.match(text, pos)
        if not m:
            raise TablePatternError(f'Cannot read table pattern "{text}".')
        parts.append(m.group(1).replace("]]", "]") if m.group(1) is not None else m.group(2).strip())
        pos = m.end()
        if pos < len(text):
            if text[pos] != ".":
                raise TablePatternError(f'Cannot read table pattern "{text}" (unexpected "{text[pos]}").')
            pos += 1
    return parts


def parse_pattern(raw: str) -> TablePattern:
    text = str(raw).strip()
    if not text:
        raise TablePatternError("Empty table pattern.")
    scope = text.split("/")
    obj = scope[-1]
    scope = [s.strip() or None for s in scope[:-1]]
    if len(scope) > 3:
        raise TablePatternError(f'"{raw}": at most resource_group/server/database/schema.table.')
    parts = _split_object(obj)
    if len(parts) == 1:
        schema, table = None, parts[0]
    elif len(parts) == 2:
        schema, table = parts
    else:
        raise TablePatternError(f'"{raw}": use table, schema.table or [schema].[table] '
                                "(put database/server in front with /).")
    rg = server = database = None
    if len(scope) == 1:
        database = scope[0]
    elif len(scope) == 2:
        server, database = scope
    elif len(scope) == 3:
        rg, server, database = scope
    return TablePattern(raw=text, table=table, schema=schema, database=database, server=server,
                        resource_group=rg)


def expand_values(values) -> list[str]:
    """['a,b', 'c'] -> ['a', 'b', 'c'] (commas outside brackets split)."""
    out = []
    for v in values or []:
        buf, depth = "", 0
        for ch in str(v):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth = max(0, depth - 1)
            if ch == "," and depth == 0:
                if buf.strip():
                    out.append(buf.strip())
                buf = ""
            else:
                buf += ch
        if buf.strip():
            out.append(buf.strip())
    return out


def read_tables_file(path: str) -> list[str]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                out.extend(expand_values([line]))
    return out


class TableSelector:
    """include (empty = every table) minus exclude, scoped per database."""

    def __init__(self, include: list[str] | None = None, exclude: list[str] | None = None,
                 schemas: list[str] | None = None):
        self.include = [parse_pattern(p) for p in expand_values(include)]
        self.exclude = [parse_pattern(p) for p in expand_values(exclude)]
        self.schemas = [s.lower() for s in expand_values(schemas)]
        self.hits: dict[str, int] = {p.raw: 0 for p in self.include}

    def applies_to_database(self, resource_group: str, server: str, database: str) -> bool:
        if not self.include:
            return True
        return any(p.matches_database(resource_group, server, database) for p in self.include)

    def select(self, resource_group: str, server: str, database: str,
               tables: list[tuple[str, str]]) -> list[tuple[str, str]]:
        inc = [p for p in self.include if p.matches_database(resource_group, server, database)]
        exc = [p for p in self.exclude if p.matches_database(resource_group, server, database)]
        if self.include and not inc:
            return []
        picked = []
        for schema, table in tables:
            if self.schemas and not any(fnmatch.fnmatchcase(schema.lower(), s) for s in self.schemas):
                continue
            if inc:
                matched = [p for p in inc if p.matches_table(schema, table)]
                if not matched:
                    continue
                for p in matched:
                    self.hits[p.raw] += 1
            if any(p.matches_table(schema, table) for p in exc):
                continue
            picked.append((schema, table))
        return picked

    def unmatched_exact(self) -> list[str]:
        """Exact (wildcard-free) patterns that matched no table anywhere."""
        return [p.raw for p in self.include if p.is_exact and self.hits.get(p.raw, 0) == 0]
