# Optional Azure Blob copy / backup (direct mode)

Off by default. Settings in config/settings.yaml under `transfer:` and `azure:`

| setting | effect |
| --- | --- |
| `transfer.mirror_to_blob: true` | after each batch is written to GCS, a copy is also uploaded to Azure Blob (demo copy; a Blob failure never fails the run) |
| `transfer.blob_first: true` (needs mirror_to_blob) | Blob is a REQUIRED backup written BEFORE GCS; a Blob failure fails the batch and GCS is untouched |
| `azure.storage_account` | the storage account name (no `.blob.core.windows.net`) |
| `azure.container` | container name, default `migration-staging` |

Secret: `AZURE_STORAGE_CONNECTION_STRING` (or `..._<REGION>`) in .env. It holds the account key; delete the account after a demo.

## How Blob is kept on the VPC tunnel (private)
Blob is only private if all four are true:
1. Azure: the storage account has a PRIVATE ENDPOINT (group `blob`) in a VNet that is connected to the Virtual WAN hub.
2. GCP: a Cloud DNS private zone `privatelink.blob.core.windows.net.` attached to `migration-vpc`, with an A record
   `<account>.privatelink.blob.core.windows.net` -> the private endpoint IP.
3. The runner resolves `<account>.blob.core.windows.net` to that private IP (public DNS answers with a CNAME to the privatelink name,
   and the private zone answers the rest) and the BGP routes send the traffic into the tunnel.
4. The code enforces it: with `transfer.mirror_to_blob: true` the Blob name is added to the private-path check
   (`--check-network`, and the start of every run). With `network.require_private: true` the run STOPS if the name resolves to a public IP.
Optional: set the storage account firewall to Deny and allow only your own IP so the portal can show the files.

## Verify / repair (run on the runner VM, venv active)

    python3 verify_blob_vs_gcs.py --bucket sts-accelerator-landing-east-us --region-slug east_us --schema dbo --table customer
    ... --repair                          # copy missing/different files GCS -> Blob
    ... --direction blob-to-gcs --repair  # restore missing files Blob -> GCS

Layouts: GCS `parquet/<schema>/<table>/<table>_part####.parquet`; Blob `<region_slug>/<schema>/<table>/<table>_part####.parquet`.

## Timing on the SUCCESS line
Each table now prints, per batch, `[eta] ... 50,000 / 75,000 row(s) (67%) | elapsed .. | est. remaining .. | est. total ..`
and at the end `===== dbo.customer: SUCCESS (3 batch(es), 75000 row(s)) | total time 2m 31s (SQL read -> BigQuery load) =====`
followed by a `[timing]` line (SQL read / Parquet write / upload / BigQuery load+merge / other). `main.py` prints the same total
under `Done:` and, for `--all`, the total for the whole batch. Turn the up-front row count off with `extraction.estimate_total: false`.
