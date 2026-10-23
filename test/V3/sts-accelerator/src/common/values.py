"""
values.py — typed (de)serialisation of primary-key and watermark values.

Checkpoints store the last committed key and the incremental watermark as
JSON. Plain str() lost the type (a datetime came back as a string with nine
fractional digits, which SQL Server's datetime2 rejects on resume), so every
value is stored as {"t": <type>, "v": <text>} and decoded back to the same
Python type pyodbc returned.
"""
import base64
import datetime
import decimal
import json
import uuid


def encode(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return {"t": "bool", "v": value}
    if isinstance(value, int):
        return {"t": "int", "v": str(value)}
    if isinstance(value, float):
        return {"t": "float", "v": repr(value)}
    if isinstance(value, decimal.Decimal):
        return {"t": "decimal", "v": str(value)}
    if isinstance(value, datetime.datetime):
        return {"t": "datetime", "v": value.isoformat()}
    if isinstance(value, datetime.date):
        return {"t": "date", "v": value.isoformat()}
    if isinstance(value, datetime.time):
        return {"t": "time", "v": value.isoformat()}
    if isinstance(value, (bytes, bytearray, memoryview)):
        return {"t": "bytes", "v": base64.b64encode(bytes(value)).decode("ascii")}
    if isinstance(value, uuid.UUID):
        return {"t": "str", "v": str(value).upper()}
    if hasattr(value, "item"):                 # numpy scalar
        return encode(value.item())
    return {"t": "str", "v": str(value)}


def decode(obj):
    if obj is None:
        return None
    if not isinstance(obj, dict) or "t" not in obj:
        return obj                             # value written by an older version: use as-is
    t, v = obj["t"], obj["v"]
    if t == "bool":
        return bool(v)
    if t == "int":
        return int(v)
    if t == "float":
        return float(v)
    if t == "decimal":
        return decimal.Decimal(v)
    if t == "datetime":
        return datetime.datetime.fromisoformat(v)
    if t == "date":
        return datetime.date.fromisoformat(v)
    if t == "time":
        return datetime.time.fromisoformat(v)
    if t == "bytes":
        return base64.b64decode(v)
    return v


def dumps(mapping: dict | None) -> str | None:
    """{column: value} -> JSON text (None stays None)."""
    if mapping is None:
        return None
    return json.dumps({k: encode(v) for k, v in mapping.items()}, sort_keys=True)


def loads(text: str | None) -> dict | None:
    if not text:
        return None
    return {k: decode(v) for k, v in json.loads(text).items()}


def dump_one(value) -> str | None:
    return None if value is None else json.dumps(encode(value))


def load_one(text: str | None):
    return None if not text else decode(json.loads(text))


def rowversion_hex(value) -> str | None:
    """bytes / '0x..' text -> '0x' + 16 hex digits (what CONVERT(binary(8), x, 1) reads)."""
    if value is None:
        return None
    if isinstance(value, (bytes, bytearray, memoryview)):
        return "0x" + bytes(value).hex().upper()
    text = str(value)
    return text if text.lower().startswith("0x") else "0x" + text
