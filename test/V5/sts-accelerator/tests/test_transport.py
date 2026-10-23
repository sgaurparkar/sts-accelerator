"""Transport: Parquet files reach GCS one at a time (direct, sts per file, sts per folder)."""
from unittest import mock

import pytest

from src.transfer.transport import TransferError, Transport

REL = "parquet/rg/srv/db/dbo/T/full/run_20261023T050000Z_ab12cd34/"


class Store:
    """Fake GCS bucket / Azure container: a set of object names."""

    def __init__(self, log, label):
        self.names, self.log, self.label = set(), log, label

    def upload_file(self, path, name):
        self.log.append((self.label, "upload", name.rsplit("/", 1)[-1]))
        self.names.add(name)

    def list_names(self, prefix):
        return sorted(n for n in self.names if n.startswith(prefix))

    def delete_names(self, names):
        for n in names:
            self.names.discard(n)
        self.log.append((self.label, "delete", len(names)))
        return len(names)

    def delete_prefix(self, prefix):
        return self.delete_names(self.list_names(prefix))


def make(mode, sts_unit="file", lose=None):
    log = []
    gcs, blob = Store(log, "gcs"), Store(log, "azure")
    sts = mock.MagicMock()

    def transfer_file(rel, name, description=""):
        log.append(("sts", "file", name))
        if name != lose:
            gcs.names.add(rel + name)
        return {"status": "SUCCESS"}

    def transfer_directory(rel, n, description=""):
        log.append(("sts", "folder", n))
        gcs.names.update(x for x in blob.names if x.startswith(rel))
        return {"status": "SUCCESS"}
    sts.transfer_file.side_effect = transfer_file
    sts.transfer_directory.side_effect = transfer_directory
    settings = {"gcp": {"gcs_bucket": "b"}, "transfer": {"mode": mode, "sts_unit": sts_unit}}
    return Transport(settings, gcs=gcs, blob=blob, sts=sts), gcs, blob, log


def names(n):
    return [f"/tmp/x__part-{i:05d}.parquet" for i in range(n)]


def test_sts_per_file_moves_each_file_before_the_next():
    t, gcs, blob, log = make("sts")
    for p in names(3):
        t.stage_file(p, REL)
        assert blob.names == set()                                # staged blob already removed
    assert [e for e in log if e[0] == "sts"] == [("sts", "file", f"x__part-{i:05d}.parquet") for i in range(3)]
    order = [(e[0], e[1]) for e in log]
    assert order[:3] == [("azure", "upload"), ("sts", "file"), ("azure", "delete")]   # one file fully, then next
    assert t.land(REL, [p.rsplit("/", 1)[-1] for p in names(3)]) == f"gs://b/{REL}*.parquet"
    assert not any(e[1] == "folder" for e in log)                 # no bulk transfer at the end
    assert t.staged_names(REL) == {f"x__part-{i:05d}.parquet" for i in range(3)}


def test_sts_per_file_fails_fast_when_a_file_does_not_arrive():
    t, gcs, blob, log = make("sts", lose="x__part-00001.parquet")
    t.stage_file(names(2)[0], REL)
    with pytest.raises(TransferError, match="part-00001.parquet is missing"):
        t.stage_file(names(2)[1], REL)


def test_direct_uploads_one_file_at_a_time():
    t, gcs, blob, log = make("direct")
    t.stage_file(names(1)[0], REL)
    assert gcs.list_names(REL) and log == [("gcs", "upload", "x__part-00000.parquet")]


def test_sts_folder_unit_keeps_one_job_at_the_end():
    t, gcs, blob, log = make("sts", sts_unit="folder")
    for p in names(3):
        t.stage_file(p, REL)
    assert gcs.names == set() and len(blob.names) == 3
    t.land(REL, [p.rsplit("/", 1)[-1] for p in names(3)])
    assert ("sts", "folder", 3) in log and len(gcs.names) == 3


def test_discard_cleans_gcs_and_azure_leftovers():
    t, gcs, blob, log = make("sts")
    for p in names(3):
        t.stage_file(p, REL)
    blob.names.add(REL + "x__part-00003.parquet")                  # uploaded, never transferred
    assert t.discard_from(REL, 2) == 2 and t.staged_names(REL) == {"x__part-00000.parquet", "x__part-00001.parquet"}


def test_bad_sts_unit():
    with pytest.raises(ValueError, match="sts_unit"):
        make("sts", sts_unit="bulk")
