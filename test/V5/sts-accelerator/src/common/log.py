"""
log.py — one logging setup for the CLI, the DAGs and the tests.

Every line carries the thread name, so parallel table runs (main.py --parallel)
stay readable, and table-scoped loggers prefix every message with
"<resource_group>/<server>/<database>:<schema>.<table>".
"""
import logging
import os
import sys

_FORMAT = "%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s"
_configured = False


def setup(level: str | None = None) -> None:
    """Idempotent. Level from STS_LOG_LEVEL (set by --log-level), else the argument, else INFO."""
    global _configured
    if _configured:
        return
    level = (os.environ.get("STS_LOG_LEVEL") or level or "INFO").upper()
    root = logging.getLogger("sts")
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter(_FORMAT))
        root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False
    # the Google / Azure SDKs are very chatty at INFO
    for noisy in ("azure", "urllib3", "google"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True


def get(name: str) -> logging.Logger:
    return logging.getLogger(f"sts.{name}")


class _Prefixed(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        return f"{self.extra['label']} | {msg}", kwargs


def for_table(name: str, label: str) -> logging.LoggerAdapter:
    return _Prefixed(get(name), {"label": label})
