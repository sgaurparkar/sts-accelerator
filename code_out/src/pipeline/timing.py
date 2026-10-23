"""Small timing helpers for the table pipeline: how long a table took end to end, where the time went,
and a running progress / time-remaining estimate while batches are being moved."""
import time


def fmt_duration(seconds) -> str:
    """12.3 -> '12.3s', 75 -> '1m 15s', 3725 -> '1h 02m 05s'."""
    seconds = max(0.0, float(seconds))
    if seconds < 60:
        return f"{seconds:.1f}s"
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}h {m:02d}m {s:02d}s" if h else f"{m}m {s:02d}s"


def timed(gen, acc: dict, key: str):
    """Pass items of `gen` through unchanged, adding the time spent waiting for each next item to acc[key]
    (here: the SQL read of each batch)."""
    while True:
        t = time.monotonic()
        try:
            item = next(gen)
        except StopIteration:
            acc[key] = acc.get(key, 0.0) + (time.monotonic() - t)
            return
        acc[key] = acc.get(key, 0.0) + (time.monotonic() - t)
        yield item


def progress_line(label: str, rows_done: int, rows_total, elapsed: float) -> str:
    """One progress line. rows_total None/0 = unknown, so no percentage or estimate is shown."""
    base = f"[eta] {label}: {rows_done:,} row(s) done | elapsed {fmt_duration(elapsed)}"
    if not rows_total or rows_total <= 0:
        return base
    pct = min(100.0, 100.0 * rows_done / rows_total)
    if rows_done <= 0:
        return f"{base[:base.index(' | ')]} of {rows_total:,} (0%) | elapsed {fmt_duration(elapsed)}"
    remaining = max(0.0, elapsed / rows_done * (rows_total - rows_done))
    return (f"[eta] {label}: {rows_done:,} / {rows_total:,} row(s) ({pct:.0f}%) | elapsed {fmt_duration(elapsed)} | "
            f"est. remaining {fmt_duration(remaining)} | est. total {fmt_duration(elapsed + remaining)}")


_NAMES = (("count", "row count"), ("sql_read", "SQL read"), ("parquet", "Parquet write"),
          ("upload", "upload (GCS/Blob)"), ("bq_load", "BigQuery load+merge"))


def breakdown_line(timings: dict, elapsed: float) -> str:
    """'[timing] SQL read 40.2s | Parquet write 3.1s | ... | other 2.0s' — 'other' = time not in any step."""
    parts = [f"{name} {fmt_duration(timings[key])}" for key, name in _NAMES if timings.get(key, 0) >= 0.05]
    other = elapsed - sum(timings.get(k, 0.0) for k, _ in _NAMES)
    if other >= 0.05:
        parts.append(f"other {fmt_duration(other)}")
    return "[timing] " + " | ".join(parts) if parts else "[timing] (no steps recorded)"
