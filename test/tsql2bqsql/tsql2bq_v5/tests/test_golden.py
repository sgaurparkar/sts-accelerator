"""Golden-file test: the 129 T-SQL feature probes must convert exactly as recorded in tests/golden/expected.

If a change is intended, regenerate the expected files and review the diff before committing:
    python tests/test_golden.py --update
"""
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "vendor"))

from tsql2bq.converter import convert_path  # noqa: E402
from tsql2bq.translator import Config  # noqa: E402

GOLDEN = Path(__file__).resolve().parent / "golden"
CFG = dict(project="my-project", default_dataset="sql_server_centralasia_salesdb", bit_columns=["IsActive", "Reviewed"])


def run(out: Path):
    return convert_path(GOLDEN / "input", out, Config(**CFG))


class GoldenProbeTests(unittest.TestCase):
    def test_probe_outputs_unchanged(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            run(tmp)
            changed = []
            for exp_file in sorted((GOLDEN / "expected").glob("*.sql")):
                got = (tmp / exp_file.name).read_text(encoding="utf-8")
                if got != exp_file.read_text(encoding="utf-8"):
                    changed.append(exp_file.name)
            self.assertEqual(changed, [], "conversions changed - review and run `python tests/test_golden.py --update`")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    if "--update" in sys.argv:
        tmp = Path(tempfile.mkdtemp())
        run(tmp)
        for f in tmp.glob("probe_*.sql"):
            shutil.copy(f, GOLDEN / "expected" / f.name)
        shutil.rmtree(tmp, ignore_errors=True)
        print("expected files updated")
    else:
        unittest.main()
