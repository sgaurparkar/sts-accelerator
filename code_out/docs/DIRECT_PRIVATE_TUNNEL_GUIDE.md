# Private VPN tunnel: how data moves region to region, and how to run it

## 1. Are we still using STS? No.

Storage Transfer Service (STS) is a Google-managed service. For an Azure Blob source it
connects from Google's side to Azure's **public** Blob endpoint. Agent pools (which run
inside your network) work only for POSIX, HDFS and S3-compatible sources, so STS cannot
use your tunnel for Azure.

The code therefore runs in **`transfer.mode: direct`**:

```
Azure SQL (per region, Private Endpoint 10.x)
      |  TDS 1433, private IP only
Azure vWAN regional hub  --hub-to-hub fabric--  NA hub (the exit hub)
      |  IPsec tunnels + BGP (2 tunnels, HA)
GCP HA VPN gateway -> Cloud Router -> migration VPC
      |
Runner VM (no external IP) in the VPC  = the only component that moves data
      |  extract (keyset pages) -> Parquet batch on local disk
      |  upload over Private Google Access (private.googleapis.com / restricted VIP)
GCS bucket  <slug>-landing-<region>   (same location as its BigQuery dataset)
      |  BigQuery load job (GCS -> staging)  -> insert-only MERGE on primary key
BigQuery  <slug>_<schema>.<table>
```

Where the data goes, step by step:

1. The runner resolves the SQL server name. The Private DNS zone `privatelink.database.windows.net`
   (mirrored in Cloud DNS) returns a `10.x` address, so the connection enters the tunnel.
2. Traffic for a remote region's SQL server crosses the vWAN hub-to-hub fabric to the hub
   that holds the tunnel, then the HA VPN, then the VPC.
3. The runner reads rows in primary-key order, writes Parquet batches, and uploads them to
   GCS through Private Google Access. No public IP is used.
4. BigQuery loads each batch into a staging table and MERGEs it into the target. Re-running
   a batch inserts nothing twice.

"Region to region" therefore has two legs: **Azure region -> Azure hub -> tunnel** (Azure
backbone, then IPsec) and **runner -> regional GCS bucket** (Google backbone). Nothing crosses
the public internet. Note that the single tunnel exit means every region's data transits
the NA hub.

## 2. How the PDF steps map to this code

| PDF step | What it builds | Code that uses it |
|---|---|---|
| vWAN + hubs + SQL private endpoints | private path from each region | `regions:` in `config/settings.yaml` (one server FQDN per region) |
| vWAN VPN gateway + VPN site | Azure side of the IPsec tunnels | not in code |
| GCP HA VPN + Cloud Router + BGP | GCP side, routes both ways | `terraform/gcp/vpc.tf` or gcloud |
| Cloud DNS private zone | names resolve to `10.x` | checked by `--check-network` |
| Runner VM in VPC | where `main.py` runs | `setup.sh`, `main.py` |
| Verify | proves the path is private | `python3 main.py --check-network` |

## 3. Fixes to the PDF commands

* `-AllowVnetToVnetTraffic` and `-EnableBgp` are **switches**. Write them without a value
  (`-EnableBgp`), not `-EnableBgp $true`.
* Custom BGP addresses go through `New-AzGatewayCustomBgpIpConfigurationObject`
  (`-IpConfigurationId Instance0` / `Instance1`) passed to `-VpnGatewayCustomBgpAddress`.
* The spare APIPA addresses 169.254.25.2 and 169.254.26.2 are outside Azure's allowed range.
  Use addresses inside **169.254.21.0-169.254.22.255**, e.g. 169.254.21.6 and 169.254.22.6.
  Each BGP session needs its own /30, unique across the VPC.
* Generate a random pre-shared key (`openssl rand -base64 32`) and do not reuse the example
  `AzureGcpVPN123!`.
* Use one GCP project ID everywhere. The PDF and `settings.yaml` say `sts-accelerator`; you
  earlier named the project `networking` (ID `networking-510109`). **Set `gcp.project_id`
  to the real ID.**

## 4. Run order

1. Build the tunnel (PDF steps, with section 3 fixes). Confirm in the portal that both
   tunnels show Connected and both BGP sessions are Established.
2. On the runner VM: `bash setup.sh`, then `cp .env.example .env` and fill the SQL logins
   (`AZURE_SQL_USERNAME_<REGION>` / `_PASSWORD_<REGION>`; no Blob or SAS variables are needed).
3. Edit `config/settings.yaml`:
   * `gcp.project_id`, and a globally unique `gcp.gcs_bucket` (same value as
     `landing_bucket_base` in `terraform/gcp/regional.tf`);
   * each region's `sql_server` FQDN, in place of the `<sql-poc-...>` placeholders;
   * keep `transfer.mode: direct` and `network.require_private: true`.
4. `python3 main.py --list-regions`
5. `python3 main.py --check-network` (add `--region "East US"` for one region). It must
   print `OK: SQL traffic from this machine uses the private path.`
   If a name resolves to a public IP, fix DNS first. If DNS is private but the probe
   times out, the problem is routing, a firewall or an NSG.
6. Small test: `python3 main.py --region "East US" --table dbo.customers`
7. Everything: `python3 main.py --region all`

If the first test run is slow to resolve names, add a Cloud DNS private zone for
`database.windows.net.` as a fallback, because the public CNAME chain can bypass a
`privatelink` zone.

## 5. Caveats

* **Not tested against live infrastructure.** Unit tests (54) pass, and the Terraform parses.
* **Single exit:** EMEA, SA and APAC SQL data transits the NA hub and is then written to a
  bucket in that region's configured location, which can cost cross-region egress and add
  latency. Check data-residency rules. A runner per continent avoids this.
* **Idle Azure hubs** are billed per hour even when not transferring; pause or delete them
  after the POC.
* The runner service account needs: `storage.objects.create`, `storage.buckets.get`
  (and `storage.buckets.create` if the bucket is not pre-created), plus BigQuery data editor
  and job user.
* `terraform/gcp/regional.tf` no longer references STS (that API needn't be enabled), and
  the unused `storage.tf` / `bigquery.tf` were removed.
