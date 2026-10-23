"""File/folder orchestration: read .sql files, convert, write BigQuery output + report."""
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .parser import ParseError, ProcDef, parse_batch, split_batches
from .tokens import tokenize
from .translator import Config, Translator, map_type, object_name, registry_key, routine_name
from .views import ViewDef, parse_view_batch

SQL_EXTS = {".sql", ".prc", ".proc", ".txt"}
OUTPUT_FILES = {"deploy_order.txt", "deploy_all.sql", "conversion_report.md", "conversion_report.json"}
UNSUPPORTED_OBJECTS = {
    "FUNCTION": "user-defined functions are not converted by this tool - rewrite as BigQuery CREATE FUNCTION / "
                "CREATE TABLE FUNCTION (procedures that call it now reference `project.dataset.<name>`)",
    "TRIGGER": "BigQuery has no triggers - move the logic into the procedures that write the table, or a scheduled query",
    "TYPE": "user-defined types / table types do not exist in BigQuery (use STRUCT / ARRAY<STRUCT>)",
    "SEQUENCE": "BigQuery has no sequences (use GENERATE_UUID() or ROW_NUMBER())",
    "SYNONYM": "BigQuery has no synonyms (use a view)",
}


def unsupported_object(batch: str):
    """(kind, name) of a CREATE FUNCTION / TRIGGER / TYPE / ... batch, else None."""
    t, _ = tokenize(batch)
    for i, tok in enumerate(t):
        if tok.up in ("CREATE", "ALTER"):
            j = i + 1
            if j + 1 < len(t) and t[j].up == "OR" and t[j + 1].up == "ALTER":
                j += 2
            if j < len(t) and t[j].up in UNSUPPORTED_OBJECTS:
                parts, k = [], j + 1
                while k < len(t) and t[k].kind in ("word", "bracket", "dquote"):
                    parts.append(t[k].text)
                    if k + 1 < len(t) and t[k + 1].is_p("."):
                        k += 2
                    else:
                        break
                return t[j].up, ".".join(parts) or "?"
    return None


ENCODING_NOTES: Dict[str, str] = {}


def read_text(path: Path) -> str:
    """SSMS 'Generate Scripts' writes UTF-16 (with BOM) by default; other tools UTF-8 or Windows-1252."""
    raw = path.read_bytes()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        return raw.decode("utf-16")
    if len(raw) > 3 and raw[1:4:2] == b"\x00\x00" and raw[0] != 0:      # UTF-16-LE without BOM
        try:
            return raw.decode("utf-16-le")
        except UnicodeDecodeError:
            pass
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("cp1252", errors="replace")
        ENCODING_NOTES[str(path)] = ("file is not UTF-8: read as Windows-1252" +
                                     (" and some bytes could not be decoded (shown as U+FFFD) - check string "
                                      "literals" if "\ufffd" in text else "") )
        return text


def parse_table_type(batch: str):
    """CREATE TYPE [schema.]name AS TABLE (col type, ...) -> ("schema.name", "col BQTYPE, ...") or None."""
    m = re.search(r"\bCREATE\s+TYPE\s+((?:\[?\w+\]?\.)?\[?\w+\]?)\s+AS\s+TABLE\s*\((.*)\)\s*;?\s*$", batch,
                  re.I | re.S)
    if not m:
        return None
    name = re.sub(r"[\[\]]", "", m.group(1))
    cols = []
    depth, cur = 0, ""
    for ch in m.group(2) + ",":
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            cur = cur.strip()
            if cur and not re.match(r"(PRIMARY|UNIQUE|INDEX|CHECK|CONSTRAINT)\b", cur, re.I):
                mm = re.match(r"\[?(\w+)\]?\s+(\w+(?:\s*\([^)]*\))?)", cur)
                if mm:
                    cols.append(f"{mm.group(1)} {map_type(mm.group(2))[0]}")
            cur = ""
        else:
            cur += ch
    if not cols:
        return None
    if "." not in name:
        name = "dbo." + name
    return name, ", ".join(cols)


@dataclass
class ProcResult:
    source: str
    procedure: str
    target: str
    output_file: str
    status: str                      # ok | review | failed
    warnings: list = field(default_factory=list)
    error: str = ""
    kind: str = "procedure"          # procedure | view


@dataclass
class Parsed:
    path: Path
    rel: Path
    proc: object                     # ProcDef | ViewDef
    kind: str = "procedure"


CONFIG_CHOICES = {
    "schema_mode": ("prefix", "drop"), "datetime_mode": ("datetime", "timestamp"), "out_param_mode": ("INOUT", "OUT"),
    "case_sensitivity": ("warn", "lower", "off"), "return_status_mode": ("drop", "out_param"),
}
CONFIG_WARNINGS: List[str] = []


def load_config(path: Optional[Path]) -> Config:
    """Read config.json. Unknown keys (usually typos such as "bit_column") and invalid values are reported instead of
    being ignored silently, because a missing setting changes the generated SQL without any other sign."""
    cfg = Config()
    CONFIG_WARNINGS.clear()
    if path and path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        for k, v in data.items():
            if k.startswith("_"):
                continue
            if not hasattr(cfg, k):
                CONFIG_WARNINGS.append(f"config.json: unknown key '{k}' ignored (check the spelling)")
                continue
            if k in CONFIG_CHOICES and str(v) not in CONFIG_CHOICES[k] and str(v).lower() not in \
                    [c.lower() for c in CONFIG_CHOICES[k]]:
                raise ValueError(f"config.json: '{k}' must be one of {', '.join(CONFIG_CHOICES[k])} (got '{v}')")
            setattr(cfg, k, v)
    if not re.fullmatch(r"(?:[a-z][a-z0-9.-]*:)?[a-z][a-z0-9-]{4,28}[a-z0-9]", str(cfg.project)):
        CONFIG_WARNINGS.append(f"project '{cfg.project}' does not look like a GCP project id (6-30 lowercase letters, "
                               f"digits, hyphens)")
    for ds in [cfg.default_dataset] + list((cfg.dataset_map or {}).values()):
        if not re.fullmatch(r"[A-Za-z0-9_]{1,1024}", str(ds)):
            CONFIG_WARNINGS.append(f"dataset '{ds}' is not a valid BigQuery dataset name (letters, digits, underscore)")
    return cfg


def discover(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    return sorted(p for p in input_path.rglob("*") if p.is_file() and p.suffix.lower() in SQL_EXTS
                  and p.name not in OUTPUT_FILES)


def dataset_key(rel: Path) -> Optional[str]:
    """First sub-folder name (e.g. salesdb/usp_x.sql -> 'salesdb')."""
    return rel.parts[0] if len(rel.parts) > 1 else None


def convert_path(input_path: Path, output_dir: Path, cfg: Config) -> List[ProcResult]:
    files = discover(input_path)
    base = input_path if input_path.is_dir() else input_path.parent
    results: List[ProcResult] = []
    parsed: List[Parsed] = []
    registry: Dict[str, ProcDef] = {}
    plain_seen: Dict[str, int] = {}

    # pass 1: parse everything (so EXEC calls can be resolved across files)
    for f in files:
        rel = f.relative_to(base)
        try:
            src = read_text(f)
        except Exception as e:  # noqa: BLE001
            results.append(ProcResult(str(rel), "-", "-", "-", "failed", error=f"cannot read file: {e}"))
            continue
        found = 0
        for batch in split_batches(src):
            try:
                proc = parse_batch(batch)
                view = parse_view_batch(batch) if proc is None else None
            except (ParseError, Exception) as e:  # noqa: BLE001
                results.append(ProcResult(str(rel), "?", "-", "-", "failed", error=f"parse error: {e}"))
                found += 1
                continue
            if proc is None and view is None:
                tt = parse_table_type(batch)
                if tt is not None:
                    cfg.table_types.setdefault(tt[0], tt[1])
                un = unsupported_object(batch)
                if un is not None:
                    found += 1
                    results.append(ProcResult(str(rel), un[1], "-", "-", "skipped",
                                              error=f"CREATE {un[0]}: {UNSUPPORTED_OBJECTS[un[0]]}",
                                              kind=un[0].lower()))
                continue
            if proc is not None:
                found += 1
                parsed.append(Parsed(f, rel, proc, "procedure"))
                # same name in two databases (folders) must not overwrite each other
                registry[registry_key(proc.schema, proc.name, dataset_key(rel))] = proc
                plain = registry_key(proc.schema, proc.name)
                plain_seen[plain] = plain_seen.get(plain, 0) + 1
                registry[plain] = proc
            elif view is not None:
                found += 1
                parsed.append(Parsed(f, rel, view, "view"))
        if not found:
            results.append(ProcResult(str(rel), "-", "-", "-", "skipped",
                                      error="no CREATE PROCEDURE / CREATE VIEW found in file"))

    for plain, n in plain_seen.items():          # ambiguous without a database: only folder-qualified lookups
        if n > 1:
            registry.pop(plain, None)

    # pass 2: translate
    tr = Translator(cfg)
    used: Dict[str, str] = {}                    # output file / BigQuery name -> source, to detect collisions
    output_dir.mkdir(parents=True, exist_ok=True)
    for p in parsed:
        ds = cfg.dataset_for(dataset_key(p.rel))
        label = f"{p.proc.schema + '.' if p.proc.schema else ''}{p.proc.name}"
        try:
            if p.kind == "view":
                sql, warns, full = tr.convert_view(p.proc, ds)
            else:
                sql, warns, full = tr.convert(p.proc, ds, registry, folder=dataset_key(p.rel))
        except Exception as e:  # noqa: BLE001
            results.append(ProcResult(str(p.rel), label, "-", "-", "failed", error=f"conversion error: {e}",
                                      kind=p.kind))
            continue
        sub = p.rel.parent
        out_name = (object_name if p.kind == "view" else routine_name)(cfg, p.proc.schema, p.proc.name) + ".sql"
        out_path = output_dir / sub / out_name
        clash = used.get(str(out_path).lower()) or used.get(full)
        if clash:
            results.append(ProcResult(str(p.rel), label, full, "-", "failed", kind=p.kind,
                                      error=f"name collision: {label} maps to the same BigQuery object / output file "
                                            f"as {clash} (check schema_mode and dataset_map) - not written"))
            continue
        used[str(out_path).lower()] = used[full] = f"{label} ({p.rel})"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(sql, encoding="utf-8")
        enc = ENCODING_NOTES.get(str(p.path))
        if enc:
            from .translator import Warning_
            warns = list(warns) + [Warning_("review", enc)]
        status = "review" if any(w.level == "review" for w in warns) else "ok"
        results.append(ProcResult(str(p.rel), label, full, str(out_path.relative_to(output_dir)), status,
                                  [{"level": w.level, "message": w.message, "snippet": w.snippet} for w in warns],
                                  kind=p.kind))
    return results


def deploy_order(results: List[ProcResult], output_dir: Path) -> List[ProcResult]:
    """Views first, then procedures; inside each group an object comes after the objects it references
    (a view that reads another view, a procedure that CALLs another procedure)."""
    done = [r for r in results if r.status in ("ok", "review")]
    text = {r.output_file: (output_dir / r.output_file).read_text(encoding="utf-8") for r in done}

    def sort(group: List[ProcResult]) -> List[ProcResult]:
        deps = {r.output_file: {o.output_file for o in group
                                if o is not r and f"`{o.target}`" in text[r.output_file]} for r in group}
        ordered, placed = [], set()
        while len(ordered) < len(group):
            ready = [r for r in group if r.output_file not in placed and deps[r.output_file] <= placed]
            if not ready:                              # cycle: keep remaining as they are
                ready = [r for r in group if r.output_file not in placed]
            for r in ready:
                ordered.append(r)
                placed.add(r.output_file)
        return ordered

    return sort([r for r in done if r.kind == "view"]) + sort([r for r in done if r.kind != "view"])


def write_report(results: List[ProcResult], output_dir: Path, cfg: Config):
    (output_dir / "conversion_report.json").write_text(
        json.dumps([r.__dict__ for r in results], indent=2), encoding="utf-8")
    order = deploy_order(results, output_dir)
    (output_dir / "deploy_order.txt").write_text("\n".join(r.output_file for r in order) + "\n", encoding="utf-8")
    (output_dir / "deploy_all.sql").write_text(
        "-- All converted objects in dependency order (views first, called procedures before callers). Review before running.\n\n" +
        "\n".join((output_dir / r.output_file).read_text(encoding="utf-8") for r in order), encoding="utf-8")
    n = lambda s: sum(1 for r in results if r.status == s)  # noqa: E731
    lines = [
        "# SQL Server -> BigQuery conversion report", "",
        f"- Target project: `{cfg.project}`",
        f"- Converted cleanly: **{n('ok')}**  (procedures: {sum(1 for r in results if r.status == 'ok' and r.kind != 'view')}, "
        f"views: {sum(1 for r in results if r.status == 'ok' and r.kind == 'view')})",
        f"- Converted, needs review: **{n('review')}**",
        f"- Failed: **{n('failed')}**   Skipped files: **{n('skipped')}**", "",
        "> `OK` only means no known problem pattern was detected. The output is syntax-converted, never executed:",
        "> dry-run every object in BigQuery and compare results with SQL Server before relying on it.", "",
    ]
    for r in results:
        icon = {"ok": "OK", "review": "REVIEW", "failed": "FAILED", "skipped": "SKIPPED"}[r.status]
        lines.append(f"## [{icon}] {r.procedure}  ({r.source})  - {r.kind}")
        if r.status in ("ok", "review"):
            lines.append(f"- Output: `{r.output_file}`  ->  `{r.target}`")
        if r.error:
            lines.append(f"- {r.error}")
        for w in r.warnings:
            tag = "REVIEW" if w["level"] == "review" else "note"
            snip = f"  \n  `{w['snippet']}`" if w["snippet"] and w["level"] == "review" else ""
            lines.append(f"- **{tag}**: {w['message']}{snip}")
        lines.append("")
    (output_dir / "conversion_report.md").write_text("\n".join(lines), encoding="utf-8")
