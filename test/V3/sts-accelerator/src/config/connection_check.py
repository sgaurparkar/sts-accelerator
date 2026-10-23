"""
connection_check.py — `python3 main.py --check-connections`

For every database in scope, without migrating anything:
  * which .env variable supplies the SQL username / password (names only, never values)
  * can we log in (SELECT @@VERSION)
  * can we read view / procedure definitions (VIEW DEFINITION) and how many there are
  * which variable supplies the Azure Storage connection string / SAS token
    (transfer.mode: sts) and when the SAS token expires
"""
import datetime
import urllib.parse

from src.config.connection import sql_secret, storage_secret
from src.topology.targets import settings_for_target

_PROBE_SQL = """
SELECT DB_NAME() AS db, CAST(SERVERPROPERTY('Edition') AS nvarchar(128)) AS edition,
       CAST(SERVERPROPERTY('ProductVersion') AS nvarchar(128)) AS version,
       SUSER_SNAME() AS login_name,
       HAS_PERMS_BY_NAME(DB_NAME(), 'DATABASE', 'VIEW DEFINITION') AS can_view_definition,
       (SELECT COUNT(*) FROM sys.objects WHERE is_ms_shipped = 0 AND type = 'U') AS tables,
       (SELECT COUNT(*) FROM sys.objects WHERE is_ms_shipped = 0 AND type = 'V') AS views,
       (SELECT COUNT(*) FROM sys.objects WHERE is_ms_shipped = 0 AND type = 'P') AS procedures,
       (SELECT COUNT(*) FROM sys.objects WHERE is_ms_shipped = 0 AND type IN ('FN', 'IF', 'TF')) AS functions,
       (SELECT COUNT(*) FROM sys.sql_modules m JOIN sys.objects o ON o.object_id = m.object_id
         WHERE o.is_ms_shipped = 0 AND o.type IN ('V', 'P') AND m.definition IS NULL) AS hidden_definitions
"""


def sas_expiry(token: str | None) -> datetime.datetime | None:
    """The `se=` (signed expiry) of a SAS token, if present."""
    if not token:
        return None
    values = urllib.parse.parse_qs(token.lstrip("?"))
    se = (values.get("se") or [None])[0]
    if not se:
        return None
    try:
        return datetime.datetime.fromisoformat(se.replace("Z", "+00:00"))
    except ValueError:
        return None


def _probe(source_cfg: dict, engine_factory=None) -> dict:
    from sqlalchemy import text
    if engine_factory is None:
        from src.config.connection import build_sql_engine
        engine_factory = build_sql_engine
    engine = engine_factory(source_cfg)
    try:
        with engine.connect() as conn:
            r = conn.execute(text(_PROBE_SQL)).fetchone()
        return {k: getattr(r, k) for k in ("db", "edition", "version", "login_name", "can_view_definition",
                                            "tables", "views", "procedures", "functions", "hidden_definitions")}
    finally:
        try:
            engine.dispose()
        except Exception:
            pass


def check_targets(settings: dict, targets: list, engine_factory=None, connect: bool = True) -> list[dict]:
    out = []
    storage_checked: set = set()
    now = datetime.datetime.now(datetime.timezone.utc)
    sts_mode = str((settings.get("transfer") or {}).get("mode") or "sts").lower() == "sts"
    for t in targets:
        ts = settings_for_target(settings, t)
        src = ts["source"]
        res = {"database": t.key, "ok": True, "problems": [], "warnings": [], "vars": {}}
        auth = str(src.get("auth") or "sql").lower()
        if auth == "sql":
            for field in ("USERNAME", "PASSWORD"):
                try:
                    _, var = sql_secret(field, src)
                    res["vars"][field.lower()] = var
                except RuntimeError as e:
                    res["ok"] = False
                    res["problems"].append(str(e))
        else:
            res["vars"]["auth"] = auth
        if sts_mode:
            account = ts["azure"].get("storage_account")
            key = (account, src.get("resource_group"), src.get("azure_location"))
            if key not in storage_checked:
                storage_checked.add(key)
                for name in ("AZURE_STORAGE_CONNECTION_STRING", "AZURE_SAS_TOKEN"):
                    try:
                        value, var = storage_secret(name, ts)
                        res["vars"][name.lower()] = var
                        if name == "AZURE_SAS_TOKEN":
                            exp = sas_expiry(value)
                            if exp is None:
                                res["warnings"].append("SAS token has no se= expiry — is it complete?")
                            elif exp <= now:
                                res["ok"] = False
                                res["problems"].append(f"SAS token expired on {exp.isoformat()}")
                            elif exp - now < datetime.timedelta(days=30):
                                res["warnings"].append(f"SAS token expires soon: {exp.isoformat()}")
                            else:
                                res["vars"]["sas_expires"] = exp.date().isoformat()
                        elif f"accountname={str(account).lower()};" not in value.lower().replace(" ", "") + ";":
                            res["warnings"].append(f"connection string is not for storage account {account}")
                    except RuntimeError as e:
                        res["ok"] = False
                        res["problems"].append(str(e))
        if connect and res["ok"]:
            try:
                info = _probe(src, engine_factory)
                res["info"] = info
                if not info.get("can_view_definition"):
                    res["warnings"].append("login lacks VIEW DEFINITION — view/procedure text will be empty "
                                           "(GRANT VIEW DEFINITION TO <login>)")
                elif info.get("hidden_definitions"):
                    res["warnings"].append(f"{info['hidden_definitions']} view(s)/procedure(s) return no "
                                           "definition (WITH ENCRYPTION or missing permission)")
            except Exception as e:
                res["ok"] = False
                res["problems"].append(f"cannot connect to {src.get('host')}/{src.get('database')}: "
                                       f"{str(e).splitlines()[0][:300]}")
        out.append(res)
    return out


def print_checks(checks: list[dict]) -> None:
    print()
    for c in checks:
        print(f"{'OK  ' if c['ok'] else 'FAIL'}  {c['database']}")
        for k, v in c["vars"].items():
            print(f"        {k:<32s} <- {v}")
        info = c.get("info")
        if info:
            print(f"        login {info['login_name']}  |  {info['edition']} {info['version']}")
            print(f"        {info['tables']} tables, {info['views']} views, {info['procedures']} procedures, "
                  f"{info['functions']} functions")
        for w in c["warnings"]:
            print(f"        WARNING {w}")
        for p in c["problems"]:
            print(f"        ERROR   {p}")
    bad = sum(1 for c in checks if not c["ok"])
    print(f"\n{len(checks) - bad} of {len(checks)} database(s) OK")
