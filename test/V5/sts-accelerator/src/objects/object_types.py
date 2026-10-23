"""
object_types.py — which SQL Server programmable objects can be exported raw.

    kind         sys.objects.type   folder / label
    view         V                  views
    procedure    P                  procedures        (T-SQL stored procedures)
    function     FN, IF, TF         functions         (scalar, inline and multi-statement TVF)
    trigger      TR                 triggers          (DML triggers on tables)

CLR objects (PC, FS, FT, TA) have no T-SQL definition and are never selected.
Default selection: views + procedures (settings.yaml objects.types).
"""

KINDS = {
    "view": ("V",),
    "procedure": ("P",),
    "function": ("FN", "IF", "TF"),
    "trigger": ("TR",),
}
FOLDERS = {"view": "views", "procedure": "procedures", "function": "functions", "trigger": "triggers"}
DEFAULT_KINDS = ["view", "procedure"]

_ALIASES = {
    "v": "view", "view": "view", "views": "view", "vw": "view",
    "p": "procedure", "proc": "procedure", "procs": "procedure", "procedure": "procedure",
    "procedures": "procedure", "sp": "procedure", "sps": "procedure", "stored_procedure": "procedure",
    "stored_procedures": "procedure", "storedprocedure": "procedure", "storedprocedures": "procedure",
    "fn": "function", "function": "function", "functions": "function", "udf": "function", "udfs": "function",
    "tr": "trigger", "trigger": "trigger", "triggers": "trigger",
}


class ObjectTypeError(ValueError):
    pass


def normalize_kinds(values, default=None) -> list[str]:
    """['views', 'sp'] -> ['view', 'procedure'];  ['all'] -> every kind;  [] -> default."""
    out: list[str] = []
    for v in values or []:
        key = str(v).strip().lower().replace("-", "_").replace(" ", "_")
        if not key:
            continue
        if key in ("all", "*"):
            kinds = list(KINDS)
        elif key in ("default", "raw"):
            kinds = list(default or DEFAULT_KINDS)
        elif key in _ALIASES:
            kinds = [_ALIASES[key]]
        else:
            raise ObjectTypeError(f'Unknown object type "{v}" — use views, procedures, functions, triggers or all.')
        for k in kinds:
            if k not in out:
                out.append(k)
    return out if out or default is None else normalize_kinds(default)


def type_codes(kinds: list[str]) -> list[str]:
    return [code for k in kinds for code in KINDS[k]]


def kind_of(type_code: str) -> str | None:
    code = str(type_code or "").strip().upper()
    return next((k for k, codes in KINDS.items() if code in codes), None)
