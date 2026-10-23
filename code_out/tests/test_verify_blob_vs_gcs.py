import sys, pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import verify_blob_vs_gcs as v


def test_identical():
    d = {"customer_part0000.parquet": (10, "a" * 64)}
    assert v.compare(d, dict(d)) == []


def test_missing_and_different():
    g = {"p0": (10, "a" * 64), "p1": (5, "b" * 64)}
    b = {"p0": (10, "c" * 64), "p2": (5, "b" * 64)}
    out = v.compare(g, b)
    assert any("DIFFERENT: p0" in x for x in out)
    assert any("MISSING in Blob: p1" in x for x in out)
    assert any("MISSING in GCS:  p2" in x for x in out)


def test_hash_chunks():
    assert v._hash_chunks([b"ab", b"c"])[0] == 3


def test_names_to_repair():
    probs = ["MISSING in Blob: p1.parquet", "DIFFERENT: p0.parquet  GCS size=1 sha=a  Blob size=2 sha=b",
             "MISSING in GCS:  p2.parquet"]
    assert v._names(probs) == ["p1.parquet", "p0.parquet"]


def test_names_missing_in_gcs():
    probs = ["MISSING in Blob: a", "MISSING in GCS:  b.parquet", "DIFFERENT: c.parquet  x"]
    assert v._names_missing_in_gcs(probs) == ["b.parquet", "c.parquet"]


def test_cli_requires_table_or_all(monkeypatch):
    monkeypatch.setenv("AZURE_STORAGE_CONNECTION_STRING", "x")
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None, raising=False)
    assert v.main(["--bucket", "b", "--region-slug", "east_us", "--container", "c"]) == 1
