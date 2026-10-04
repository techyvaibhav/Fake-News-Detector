import yaml
from urllib.parse import urlparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REG = {s["domain"]: s for s in yaml.safe_load((ROOT / "sources.yaml").read_text())["sources"]}
CFG = yaml.safe_load((ROOT / "config.yaml").read_text())

def lookup(url: str):
    """Match a URL to registry entry (subdomains ok); default Tier 3 'other'."""
    host = (urlparse(url).hostname or "").removeprefix("www.")
    for d, s in REG.items():
        if host == d or host.endswith("." + d):
            return s
    return {"domain": host, "name": host, "tier": 3, "type": "other", "country": "?"}
