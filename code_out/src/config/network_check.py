"""
network_check.py

Proves that the pipeline is really using the Azure <-> GCP VPN tunnel.

In "direct" mode the runner VM sits inside migration-vpc-global and reads each
region's Azure SQL server. That read only crosses the tunnel when the SQL server
name resolves to the server's PRIVATE endpoint address (answered by the Cloud DNS
private zone privatelink.database.windows.net.) AND that address is actually
routable through the tunnel. If DNS still returns the public address, the traffic
would silently go over the internet instead (or simply fail, because the servers
have public access disabled).

Two checks, both reported per name:

  1. DNS   - the name resolves to private (RFC 1918) addresses only.
  2. Route - a TCP connection to that address on the service port (1433 for SQL,
             443 for Blob) succeeds. That is what proves BGP routes, the IPsec
             tunnel, the hub and the Azure NSG/firewall all line up. Always done
             by `--check-network`; done before every run when
             network.probe_tcp is true.

    python main.py --region "East US" --check-network

Settings (config/settings.yaml):
    network:
      require_private: true     # refuse to run when a name resolves to a public address
      probe_tcp: true           # also refuse to run when the private address is not reachable
Env override:  REQUIRE_PRIVATE_PATH=false
"""
import ipaddress
import os
import socket

_checked: set = set()

VALID_MODES = ("direct", "sts")
_SERVICE_PORTS = {"Azure SQL server": 1433, "Azure Blob storage": 443}


def _resolve(host: str) -> list:
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return sorted({i[4][0] for i in infos})


def _is_private(ip: str) -> bool:
    return ipaddress.ip_address(ip).is_private


def _probe(ip: str, port: int, timeout: float) -> tuple[bool, str | None]:
    """TCP connect test. Returns (reachable, error_text)."""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, None
    except OSError as exc:
        return False, str(exc) or exc.__class__.__name__


def transfer_mode(settings: dict) -> str:
    """'direct' = runner writes Parquet straight to GCS (all private, no Blob/STS).
    'sts' = Parquet goes to Azure Blob and Storage Transfer Service copies it (public hop).
    Anything else is an error: a typo such as "direkt" must not silently fall back to STS."""
    mode = str((settings.get("transfer") or {}).get("mode", "sts")).strip().lower()
    if mode not in VALID_MODES:
        raise RuntimeError(f'transfer.mode "{mode}" in config/settings.yaml is not valid — '
                           f'use "direct" or "sts".')
    return mode


def mirror_to_blob(settings: dict) -> bool:
    """True = direct mode ALSO drops a copy of each Parquet batch into Azure Blob, for demonstration
    only. Nothing reads it back; GCS stays the real destination and a Blob failure never fails a run."""
    return bool((settings.get("transfer") or {}).get("mirror_to_blob", False))


def blob_first(settings: dict) -> bool:
    """True = with mirror_to_blob on, each batch goes to Azure Blob FIRST (required backup) and only then
    to GCS. A Blob failure then fails the batch before GCS is touched, so Blob is never behind GCS."""
    return mirror_to_blob(settings) and bool((settings.get("transfer") or {}).get("blob_first", False))


def endpoints(settings: dict) -> list:
    """The names whose resolution decides whether the data path uses the tunnel."""
    host = str(settings["source"]["host"]).split(":")[0].strip()
    out = [("Azure SQL server", host)]
    if transfer_mode(settings) != "direct" or mirror_to_blob(settings):
        account = settings["azure"]["storage_account"]
        out.append(("Azure Blob storage", f"{account}.blob.core.windows.net"))
    return out


def _port_for(label: str, settings: dict) -> int:
    if label == "Azure SQL server":
        host = str(settings["source"]["host"]).strip()
        if ":" in host:
            try:
                return int(host.rsplit(":", 1)[1])
            except ValueError:
                pass
    return _SERVICE_PORTS.get(label, 443)


def check_private_path(settings: dict, probe: bool = False, timeout: float = 5.0) -> list:
    """One result dict per endpoint:
       label, host, ips, private (DNS check), port, reachable (None = not probed), error."""
    results = []
    for label, host in endpoints(settings):
        port = _port_for(label, settings)
        try:
            ips = _resolve(host)
            private = bool(ips) and all(_is_private(ip) for ip in ips)
            result = {"label": label, "host": host, "ips": ips, "private": private,
                      "port": port, "reachable": None, "error": None}
            if probe and private:
                errors = []
                for ip in ips:
                    ok, err = _probe(ip, port, timeout)
                    if ok:
                        result["reachable"] = True
                        break
                    errors.append(f"{ip}:{port} {err}")
                else:
                    result["reachable"] = False
                    result["error"] = "; ".join(errors)
        except OSError as exc:
            result = {"label": label, "host": host, "ips": [], "private": False,
                      "port": port, "reachable": None, "error": str(exc)}
        results.append(result)
    return results


def _ok(result: dict) -> bool:
    """Private address, and (when it was probed) reachable."""
    return bool(result["private"]) and result.get("reachable") is not False


def all_ok(results: list) -> bool:
    return all(_ok(r) for r in results)


def require_private(settings: dict) -> bool:
    env = os.environ.get("REQUIRE_PRIVATE_PATH")
    if env is not None and env.strip():
        return env.strip().lower() not in ("0", "false", "no")
    return bool((settings.get("network") or {}).get("require_private", False))


def format_report(results: list) -> str:
    lines = []
    for r in results:
        if not r["ips"] and r["error"]:
            state = f"NOT RESOLVABLE ({r['error']})"
        elif not r["private"]:
            state = "PUBLIC (bypasses the VPN tunnel)"
        elif r.get("reachable") is True:
            state = f"private, port {r['port']} reachable (through the VPN tunnel)"
        elif r.get("reachable") is False:
            state = f"private address but port {r['port']} NOT reachable ({r['error']})"
        else:
            state = "private (DNS only - route not probed)"
        lines.append(f"  {r['label']:20s} {r['host']:60s} {','.join(r['ips']) or '-':18s} {state}")
    return "\n".join(lines)


def ensure_private_path(settings: dict) -> None:
    """Called once per region before a run starts. Raises RuntimeError when private
    addresses are required and a name resolves to a public one (or not at all), or
    - with network.probe_tcp - when the private address cannot be reached."""
    if not (settings.get("source") or {}).get("host"):
        return
    if transfer_mode(settings) != "direct" and not (settings.get("azure") or {}).get("storage_account"):
        return   # nothing to check (e.g. minimal settings in unit tests)
    key = (settings["source"].get("region"), settings["source"].get("host"),
           (settings.get("azure") or {}).get("storage_account"))
    if key in _checked:
        return
    probe = bool((settings.get("network") or {}).get("probe_tcp", False))
    results = check_private_path(settings, probe=probe)
    print("[network_check] VPN path check:\n" + format_report(results))
    not_private = [r for r in results if not r["private"]]
    unreachable = [r for r in results if r["private"] and r.get("reachable") is False]
    if (not_private or unreachable) and require_private(settings):
        if not_private:
            names = ", ".join(r["host"] for r in not_private)
            raise RuntimeError(
                f"Not using the VPN tunnel: {names} did not resolve to a private address. "
                "Run this from a machine inside migration-vpc-global and check the Cloud DNS private "
                "zone privatelink.database.windows.net. (setup guide step 7-8). To skip this check set "
                "network.require_private: false or REQUIRE_PRIVATE_PATH=false.")
        names = ", ".join(f"{r['host']}:{r['port']}" for r in unreachable)
        raise RuntimeError(
            f"Private address found but not reachable: {names}. DNS is right, so look at the "
            "tunnel/BGP state (gcloud compute routers get-status), the learned routes for the Azure "
            "VNet range, and the Azure NSG / private endpoint on that server.")
    if not_private or unreachable:
        print("[network_check] WARNING: traffic is NOT going through the VPN tunnel (check not enforced).")
    _checked.add(key)
