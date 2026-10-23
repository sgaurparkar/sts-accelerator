"""
location_mapping.py

Azure location -> GCP location, so every BigQuery dataset and its GCS
landing bucket is created in the GCP region that matches (or is closest to)
the Azure region the source SQL server lives in.

The Azure location is read from the server itself (Azure Resource Manager
returns the programmatic name, e.g. "centralindia", "eastus2"); display names
("Central India", "East US 2") are accepted too — case, spaces, hyphens and
underscores are ignored.

Two strategies (gcp.location_mapping.strategy in config/settings.yaml):

  closest       (default) the single GCP region geographically closest to the
                Azure region:  centralindia -> asia-south1, eastus -> us-east4
  multi_region  BigQuery/GCS multi-region where one exists for that geography
                (US / EU); falls back to `closest` elsewhere (e.g. India has
                no multi-region, so it stays asia-south1)

Any Azure location can be pinned explicitly with
gcp.location_mapping.overrides:  { "Central India": asia-south2 }.

BigQuery cannot load GCS data across locations, so the bucket and the
dataset of one target ALWAYS share the location chosen here.
"""
import re


class LocationMappingError(ValueError):
    """Azure location that cannot be mapped to a GCP location."""


def normalize_azure_location(location) -> str:
    """'Central India' / 'central-india' / 'centralindia' -> 'centralindia'."""
    return re.sub(r"[^a-z0-9]", "", str(location or "").lower())


# Azure programmatic location name -> closest GCP region.
AZURE_TO_GCP_CLOSEST: dict[str, str] = {
    # ---- India -------------------------------------------------------------
    "centralindia": "asia-south1",       # Pune      -> Mumbai
    "westindia": "asia-south1",          # Mumbai    -> Mumbai
    "southindia": "asia-south1",         # Chennai   -> Mumbai
    "jioindiawest": "asia-south1",       # Jamnagar  -> Mumbai
    "jioindiacentral": "asia-south1",    # Nagpur    -> Mumbai
    # ---- United States -----------------------------------------------------
    "eastus": "us-east4",                # Virginia  -> N. Virginia
    "eastus2": "us-east4",               # Virginia  -> N. Virginia
    "centralus": "us-central1",          # Iowa      -> Iowa
    "northcentralus": "us-east5",        # Illinois  -> Columbus
    "southcentralus": "us-south1",       # Texas     -> Dallas
    "westcentralus": "us-west3",         # Wyoming   -> Salt Lake City
    "westus": "us-west2",                # California-> Los Angeles
    "westus2": "us-west1",               # Washington-> Oregon
    "westus3": "us-west4",               # Arizona   -> Las Vegas
    # ---- Americas (other) --------------------------------------------------
    "canadacentral": "northamerica-northeast2",   # Toronto
    "canadaeast": "northamerica-northeast1",      # Quebec -> Montreal
    "brazilsouth": "southamerica-east1",          # Sao Paulo
    "brazilsoutheast": "southamerica-east1",
    "mexicocentral": "northamerica-south1",       # Queretaro
    "chilecentral": "southamerica-west1",         # Santiago
    # ---- Europe ------------------------------------------------------------
    "northeurope": "europe-west2",       # Ireland   -> London
    "westeurope": "europe-west4",        # Netherlands
    "uksouth": "europe-west2",           # London
    "ukwest": "europe-west2",
    "francecentral": "europe-west9",     # Paris
    "francesouth": "europe-west9",
    "germanywestcentral": "europe-west3",  # Frankfurt
    "germanynorth": "europe-west10",     # Berlin
    "switzerlandnorth": "europe-west6",  # Zurich
    "switzerlandwest": "europe-west6",
    "norwayeast": "europe-north1",
    "norwaywest": "europe-north1",
    "swedencentral": "europe-north1",
    "polandcentral": "europe-central2",  # Warsaw
    "italynorth": "europe-west8",        # Milan
    "spaincentral": "europe-southwest1", # Madrid
    # ---- Asia Pacific ------------------------------------------------------
    "eastasia": "asia-east2",            # Hong Kong
    "southeastasia": "asia-southeast1",  # Singapore
    "japaneast": "asia-northeast1",      # Tokyo
    "japanwest": "asia-northeast2",      # Osaka
    "koreacentral": "asia-northeast3",   # Seoul
    "koreasouth": "asia-northeast3",
    "australiaeast": "australia-southeast1",       # Sydney
    "australiasoutheast": "australia-southeast2",  # Melbourne
    "australiacentral": "australia-southeast1",
    "australiacentral2": "australia-southeast1",
    "indonesiacentral": "asia-southeast2",         # Jakarta
    "malaysiawest": "asia-southeast1",
    "newzealandnorth": "australia-southeast1",
    # ---- Middle East / Africa ---------------------------------------------
    "uaenorth": "me-central1",
    "uaecentral": "me-central1",
    "qatarcentral": "me-central1",       # Doha
    "israelcentral": "me-west1",         # Tel Aviv
    "southafricanorth": "africa-south1", # Johannesburg
    "southafricawest": "africa-south1",
}

# Geography -> BigQuery/GCS multi-region (used by strategy=multi_region).
_US = {"eastus", "eastus2", "centralus", "northcentralus", "southcentralus",
       "westcentralus", "westus", "westus2", "westus3"}
_EU = {"northeurope", "westeurope", "francecentral", "francesouth", "germanywestcentral",
       "germanynorth", "switzerlandnorth", "switzerlandwest", "norwayeast", "norwaywest",
       "swedencentral", "polandcentral", "italynorth", "spaincentral"}

_GCP_LOCATION_RE = re.compile(r"^(?:[a-z]+-[a-z]+[0-9]+|us|eu)$", re.I)


def _mapping_cfg(settings: dict) -> dict:
    return ((settings.get("gcp") or {}).get("location_mapping") or {})


def resolve_gcp_location(azure_location: str, settings: dict | None = None) -> str:
    """GCP location for one Azure location, honouring overrides + strategy."""
    cfg = _mapping_cfg(settings or {})
    key = normalize_azure_location(azure_location)
    if not key:
        raise LocationMappingError("Azure location is empty — cannot choose a GCP location.")

    overrides = {normalize_azure_location(k): str(v).strip()
                 for k, v in (cfg.get("overrides") or {}).items() if v}
    if key in overrides:
        loc = overrides[key]
    else:
        strategy = str(cfg.get("strategy") or "closest").lower()
        if strategy not in ("closest", "multi_region"):
            raise LocationMappingError(
                f'gcp.location_mapping.strategy "{strategy}" is not valid — use closest or multi_region.')
        if strategy == "multi_region" and key in _US:
            loc = "US"
        elif strategy == "multi_region" and key in _EU:
            loc = "EU"
        elif key in AZURE_TO_GCP_CLOSEST:
            loc = AZURE_TO_GCP_CLOSEST[key]
        else:
            raise LocationMappingError(
                f'No GCP location is known for Azure location "{azure_location}". Add it under '
                f'gcp.location_mapping.overrides in config/settings.yaml, e.g.  "{azure_location}": us-central1')

    if not _GCP_LOCATION_RE.match(loc):
        raise LocationMappingError(f'"{loc}" (for Azure location "{azure_location}") is not a valid '
                                   "GCP location (expected e.g. asia-south1, us-east4, US, EU).")
    return loc
