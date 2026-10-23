"""
run_spec.py — a "job" is one selection + one load mode. A run is a list of jobs.

From the command line you get exactly one job; with --spec run.yaml you can mix
anything in one run, e.g. one database in full, three tables of another
incrementally and one month of a third table as a partial reload:

    defaults:                 # optional, applied to every job
      mode: full
    jobs:
      - name: sales-full
        resource_groups: [ResourceGroup1]
        databases: [SalesDB]
        tables: ["dbo.*"]
        exclude_tables: ["dbo.tmp_*"]
      - name: hr-incremental
        databases: [HRDB]
        tables: [dbo.Employees, dbo.Payroll]
        mode: incremental
      - name: finance-q1
        databases: [FinanceDB]
        tables: [dbo.Transactions]
        mode: partial
        date_column: TxnDate
        from: 2026-01-01
        to: 2026-04-01          # exclusive
        replace_range: true
      - name: sales-raw-objects     # views + stored procedures, raw T-SQL (no translation)
        databases: [SalesDB]
        objects: [views, procedures]
        object_names: ["dbo.*"]     # optional, same pattern syntax as tables
        exclude_objects: ["dbo.tmp_*"]

A job migrates tables (tables / all_tables), raw objects (objects), or both.

Load modes
  auto         per table from config/tables_override.yaml (load_mode), default full
  full         complete copy. A fresh run truncates the BigQuery table first;
               a failed run resumes from its last committed chunk. A COMPLETED
               table is skipped unless --force.
  incremental  only rows changed since the last successful run (watermark:
               rowversion, an updated-at column, or an ever-increasing key),
               upserted by primary/unique key.
  partial      only rows matching --where and/or --date-column/--from/--to,
               upserted by key (--replace-range first deletes that date range
               in BigQuery, so deletes in the range are mirrored too).
"""
import datetime
from dataclasses import asdict, dataclass, field

import yaml

from src.selection.table_selector import expand_values, read_tables_file

MODES = ("auto", "full", "incremental", "partial")
MODE_ALIASES = {"delta": "incremental", "cdc": "incremental", "filtered": "partial", "subset": "partial",
                "complete": "full", "reload": "full"}
STAGES = ("bq", "gcs")


class RunSpecError(ValueError):
    pass


def normalize_mode(mode: str | None) -> str:
    m = str(mode or "auto").strip().lower()
    m = MODE_ALIASES.get(m, m)
    if m not in MODES:
        raise RunSpecError(f'Unknown load mode "{mode}" — use one of {", ".join(MODES)}.')
    return m


def _date_text(value) -> str | None:
    if value is None or value == "":
        return None
    if isinstance(value, (datetime.date, datetime.datetime)):
        return value.isoformat()
    text = str(value).strip()
    try:
        datetime.datetime.fromisoformat(text)
    except ValueError:
        raise RunSpecError(f'"{text}" is not a date/timestamp (use YYYY-MM-DD or YYYY-MM-DDTHH:MM:SS).') from None
    return text


@dataclass
class Job:
    name: str = "cli"
    # database scope (same meaning as azure_topology filters; empty = everything in scope)
    resource_groups: list[str] = field(default_factory=list)
    servers: list[str] = field(default_factory=list)
    databases: list[str] = field(default_factory=list)
    azure_locations: list[str] = field(default_factory=list)
    # table scope
    all_tables: bool = False
    tables: list[str] = field(default_factory=list)
    exclude_tables: list[str] = field(default_factory=list)
    schemas: list[str] = field(default_factory=list)
    only_modes: list[str] = field(default_factory=list)   # with mode auto: keep tables whose resolved mode is one of these
    # load behaviour
    mode: str = "auto"
    where: str | None = None
    date_column: str | None = None
    date_from: str | None = None
    date_to: str | None = None
    replace_range: bool = False
    cutoff_date: str | None = None          # overrides extraction.cutoff_date for full loads ("none" disables)
    force: bool = False
    stage: str = "bq"
    # raw views / procedures (src/objects): kinds to export, empty = no objects
    objects: list[str] = field(default_factory=list)
    object_names: list[str] = field(default_factory=list)
    exclude_objects: list[str] = field(default_factory=list)

    @property
    def has_tables(self) -> bool:
        return bool(self.all_tables or self.tables)

    @property
    def has_objects(self) -> bool:
        return bool(self.objects)

    def validate(self) -> "Job":
        self.mode = normalize_mode(self.mode)
        self.only_modes = [normalize_mode(m) for m in self.only_modes]
        if self.stage not in STAGES:
            raise RunSpecError(f'{self.name}: stage must be bq or gcs, not "{self.stage}".')
        try:
            from src.objects.object_types import ObjectTypeError, normalize_kinds
            self.objects = normalize_kinds(self.objects)
        except ObjectTypeError as e:
            raise RunSpecError(f"{self.name}: {e}") from None
        if (self.object_names or self.exclude_objects) and not self.objects:
            raise RunSpecError(f"{self.name}: object_names / exclude_objects need objects: [views, procedures, ...].")
        if not self.all_tables and not self.tables and not self.objects:
            raise RunSpecError(f"{self.name}: choose tables (tables: [...]), all tables (all_tables: true) "
                               "and/or raw objects (objects: [views, procedures]).")
        has_filter = bool(self.where or self.date_column or self.date_from or self.date_to)
        if has_filter and self.mode != "partial":
            raise RunSpecError(f"{self.name}: --where / --date-column / --from / --to need --mode partial.")
        if self.mode == "partial" and not has_filter:
            raise RunSpecError(f"{self.name}: --mode partial needs --where and/or --date-column with --from/--to.")
        if (self.date_from or self.date_to) and not self.date_column:
            raise RunSpecError(f"{self.name}: --from/--to need --date-column.")
        if self.date_column and not (self.date_from or self.date_to):
            raise RunSpecError(f"{self.name}: --date-column needs --from and/or --to.")
        if self.replace_range and not self.date_column:
            raise RunSpecError(f"{self.name}: --replace-range needs --date-column with --from/--to.")
        self.date_from, self.date_to = _date_text(self.date_from), _date_text(self.date_to)
        if self.date_from and self.date_to and self.date_from >= self.date_to:
            raise RunSpecError(f"{self.name}: --from must be before --to (--to is exclusive).")
        return self

    def filters(self) -> dict:
        return {"resource_groups": self.resource_groups, "servers": self.servers,
                "databases": self.databases, "azure_locations": self.azure_locations}

    def partial_filter(self) -> dict | None:
        if self.mode != "partial":
            return None
        return {"where": self.where, "date_column": self.date_column, "from": self.date_from,
                "to": self.date_to, "replace_range": self.replace_range}

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Job":
        return cls(**data).validate()


_KEY_ALIASES = {"from": "date_from", "to": "date_to", "exclude": "exclude_tables", "schema": "schemas",
                "resource_group": "resource_groups", "server": "servers", "database": "databases",
                "azure_location": "azure_locations", "table": "tables", "all": "all_tables",
                "object": "objects", "object_types": "objects", "object_name": "object_names",
                "exclude_object": "exclude_objects"}
_LIST_FIELDS = {"resource_groups", "servers", "databases", "azure_locations", "tables", "exclude_tables",
                "schemas", "only_modes", "objects", "object_names", "exclude_objects"}


def job_from_mapping(data: dict, defaults: dict | None = None, index: int = 0, base_dir: str = ".") -> Job:
    merged = {}
    for source in (defaults or {}, data or {}):
        for k, v in source.items():
            merged[_KEY_ALIASES.get(k, k)] = v
    tables_file = merged.pop("tables_file", None)
    known = set(Job.__dataclass_fields__)
    unknown = set(merged) - known
    if unknown:
        raise RunSpecError(f"Job {merged.get('name') or index + 1}: unknown key(s) {sorted(unknown)}.")
    for k in _LIST_FIELDS & set(merged):
        merged[k] = expand_values(merged[k] if isinstance(merged[k], list) else [merged[k]])
    if tables_file:
        import os
        path = tables_file if os.path.isabs(tables_file) else os.path.join(base_dir, tables_file)
        merged["tables"] = list(merged.get("tables") or []) + read_tables_file(path)
    if merged.get("tables") == ["*"] or merged.get("tables") == ["*.*"]:
        merged["tables"], merged["all_tables"] = [], True
    merged.setdefault("name", f"job{index + 1}")
    for k in ("date_from", "date_to", "cutoff_date"):
        if k in merged and isinstance(merged[k], (datetime.date, datetime.datetime)):
            merged[k] = merged[k].isoformat()
    return Job(**merged).validate()


def load_spec(path: str) -> list[Job]:
    import os
    try:
        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except FileNotFoundError:
        raise RunSpecError(f"Run spec not found: {path}") from None
    if isinstance(data, list):
        data = {"jobs": data}
    jobs = data.get("jobs")
    if not isinstance(jobs, list) or not jobs:
        raise RunSpecError(f"{path}: needs a non-empty `jobs:` list.")
    defaults = data.get("defaults") or {}
    base_dir = os.path.dirname(os.path.abspath(path))
    built = [job_from_mapping(j, defaults, i, base_dir) for i, j in enumerate(jobs)]
    names = [j.name for j in built]
    if len(set(names)) != len(names):
        raise RunSpecError(f"{path}: job names must be unique ({names}).")
    return built
