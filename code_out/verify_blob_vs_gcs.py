"""
verify_blob_vs_gcs.py  -  prove the demo copy in Azure Blob is byte-identical to the file in GCS.

Run on the runner VM (inside the VPC), venv active:

  python3 verify_blob_vs_gcs.py --bucket sts-accelerator-landing-east-us \
      --region-slug east_us --schema dbo --table customer

Layouts compared (same file name on both sides):
  GCS : gs://<bucket>/parquet/<schema>/<table>/<table>_part####.parquet
  Blob: <container>/<region_slug>/<schema>/<table>/<table>_part####.parquet
Add --repair to copy any file that is missing or different from GCS into Blob (GCS is the source of truth),
then it checks again. Needs AZURE_STORAGE_CONNECTION_STRING (or ..._<REGION>) in .env.
Exit code 0 = all match, 1 = not.
"""
import argparse
import hashlib
import os
import sys

CHUNK = 8 * 1024 * 1024


def compare(gcs: dict, blob: dict) -> list:
    """gcs / blob: {file name: (size, sha256)}.  Returns a list of problem strings (empty = identical)."""
    problems = []
    for name in sorted(set(gcs) | set(blob)):
        if name not in blob:
            problems.append(f"MISSING in Blob: {name}")
        elif name not in gcs:
            problems.append(f"MISSING in GCS:  {name}")
        elif gcs[name] != blob[name]:
            problems.append(f"DIFFERENT: {name}  GCS size={gcs[name][0]} sha={gcs[name][1][:12]}  "
                            f"Blob size={blob[name][0]} sha={blob[name][1][:12]}")
    return problems


def _hash_chunks(chunks) -> tuple:
    h, n = hashlib.sha256(), 0
    for c in chunks:
        h.update(c); n += len(c)
    return n, h.hexdigest()


def read_gcs(project, bucket, schema, table) -> dict:
    from google.cloud import storage
    client = storage.Client(project=project) if project else storage.Client()
    out = {}
    for b in client.list_blobs(bucket, prefix=f"parquet/{schema}/{table}/"):
        name = b.name.rsplit("/", 1)[-1]
        if name.endswith(".parquet"):
            with b.open("rb") as f:
                out[name] = _hash_chunks(iter(lambda: f.read(CHUNK), b""))
    return out


def read_blob(conn_str, container, region_slug, schema, table) -> dict:
    from azure.storage.blob import BlobServiceClient
    cc = BlobServiceClient.from_connection_string(conn_str).get_container_client(container)
    out = {}
    for b in cc.list_blobs(name_starts_with=f"{region_slug}/{schema}/{table}/"):
        name = b.name.rsplit("/", 1)[-1]
        if name.endswith(".parquet"):
            out[name] = _hash_chunks(cc.download_blob(b.name).chunks())
    return out


def repair(conn_str, project, bucket, container, region_slug, schema, table, names) -> None:
    """Copy each named file GCS -> Blob (overwrite). Runs on the runner, so it uses the same private path."""
    import tempfile
    from google.cloud import storage
    from azure.storage.blob import BlobServiceClient
    gb = storage.Client(project=project) if project else storage.Client()
    cc = BlobServiceClient.from_connection_string(conn_str).get_container_client(container)
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, name)
            gb.bucket(bucket).blob(f"parquet/{schema}/{table}/{name}").download_to_filename(local)
            with open(local, "rb") as f:
                cc.upload_blob(f"{region_slug}/{schema}/{table}/{name}", f, overwrite=True, max_concurrency=4)
        print(f"  repaired: {name}")


def repair_to_gcs(project, bucket, conn_str, container, region_slug, schema, table, names) -> None:
    """Copy each named file Blob -> GCS (overwrite). Blob is then the source of truth (backup restore)."""
    import tempfile
    from google.cloud import storage
    from azure.storage.blob import BlobServiceClient
    gb = storage.Client(project=project) if project else storage.Client()
    cc = BlobServiceClient.from_connection_string(conn_str).get_container_client(container)
    for name in names:
        with tempfile.TemporaryDirectory() as tmp:
            local = os.path.join(tmp, name)
            with open(local, "wb") as f:
                for chunk in cc.download_blob(f"{region_slug}/{schema}/{table}/{name}").chunks():
                    f.write(chunk)
            gb.bucket(bucket).blob(f"parquet/{schema}/{table}/{name}").upload_from_filename(local, checksum="md5")
        print(f"  restored to GCS: {name}")


def _names_missing_in_gcs(problems) -> list:
    return [p.split(":", 1)[1].split()[0] for p in problems if p.startswith(("MISSING in GCS", "DIFFERENT"))]


def _names(problems) -> list:
    return [p.split(":", 1)[1].split()[0] for p in problems if p.startswith(("MISSING in Blob", "DIFFERENT"))]


def check_table(a, conn, table) -> int:
    print(f"\n=== {a.schema}.{table} ===")
    gcs = read_gcs(a.project, a.bucket, a.schema, table)
    blob = read_blob(conn, a.container, a.region_slug, a.schema, table)
    print(f"GCS files: {len(gcs)}   Blob files: {len(blob)}")
    for name in sorted(gcs):
        print(f"  {name}  {gcs[name][0]:>12,} bytes  sha256 {gcs[name][1][:16]}")
    problems = compare(gcs, blob)
    if problems and a.repair:
        if a.direction == "blob-to-gcs":
            fix = _names_missing_in_gcs(problems)
            if fix:
                print(f"\nRestoring {len(fix)} file(s) from Blob to GCS ...")
                repair_to_gcs(a.project, a.bucket, conn, a.container, a.region_slug, a.schema, table, fix)
                gcs = read_gcs(a.project, a.bucket, a.schema, table)
        else:
            fix = _names(problems)
            if fix:
                print(f"\nRepairing {len(fix)} file(s) from GCS ...")
                repair(conn, a.project, a.bucket, a.container, a.region_slug, a.schema, table, fix)
                blob = read_blob(conn, a.container, a.region_slug, a.schema, table)
        problems = compare(gcs, blob)
    if not gcs and not blob:
        print("Nothing found on either side - check --bucket, --region-slug, --schema, --table."); return 1
    if problems:
        print("\nMISMATCH:"); [print("  " + p) for p in problems]; return 1
    print(f"\nOK: all {len(gcs)} file(s) are byte-identical in GCS and Azure Blob.")
    return 0


def discover_tables(a, conn) -> list:
    from azure.storage.blob import BlobServiceClient
    from google.cloud import storage
    names = set()
    cc = BlobServiceClient.from_connection_string(conn).get_container_client(a.container)
    for b in cc.list_blobs(name_starts_with=f"{a.region_slug}/{a.schema}/"):
        parts = b.name.split("/")
        if len(parts) >= 4: names.add(parts[2])
    client = storage.Client(project=a.project) if a.project else storage.Client()
    for b in client.list_blobs(a.bucket, prefix=f"parquet/{a.schema}/"):
        parts = b.name.split("/")
        if len(parts) >= 4: names.add(parts[2])
    return sorted(names)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--bucket", required=True, help="the region's landing bucket")
    ap.add_argument("--region-slug", required=True, help="e.g. east_us (the Blob folder prefix)")
    ap.add_argument("--schema", default="dbo")
    ap.add_argument("--table", default=None, help="one table; omit with --all-tables")
    ap.add_argument("--all-tables", action="store_true", help="check every table found under the region in Blob and GCS")
    ap.add_argument("--container", default=None, help="default: azure.container from config/settings.yaml")
    ap.add_argument("--project", default=None)
    ap.add_argument("--repair", action="store_true", help="copy missing/different files, then check again")
    ap.add_argument("--direction", choices=["gcs-to-blob", "blob-to-gcs"], default="gcs-to-blob",
                    help="which side is the source of truth when repairing (default gcs-to-blob)")
    a = ap.parse_args(argv)

    from dotenv import load_dotenv
    load_dotenv()
    slug = a.region_slug.upper()
    conn = os.environ.get(f"AZURE_STORAGE_CONNECTION_STRING_{slug}") or os.environ.get("AZURE_STORAGE_CONNECTION_STRING")
    if not conn:
        print("AZURE_STORAGE_CONNECTION_STRING is not set in .env"); return 1

    if not a.container:
        try:
            import yaml
            a.container = yaml.safe_load(open("config/settings.yaml"))["azure"]["container"]
        except Exception:
            a.container = "parquet-backup"
    if a.all_tables:
        tables = discover_tables(a, conn)
        if not tables:
            print("No tables found on either side."); return 1
        bad = [t for t in tables if check_table(a, conn, t) != 0]
        print(f"\nSUMMARY: {len(tables)-len(bad)}/{len(tables)} tables OK" + (f"; MISMATCH: {', '.join(bad)}" if bad else ""))
        return 1 if bad else 0
    if not a.table:
        print("Give --table <name> or --all-tables"); return 1
    return check_table(a, conn, a.table)


if __name__ == "__main__":
    sys.exit(main())
