"""Tranco top sites リストから proxy 検証用サイトリストを生成する.

Usage:
    uv run python scripts/generate_check_sites.py
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

import requests

_TRANCO_URL = "https://tranco-list.eu/top-1m.csv.zip"
_TARGET_COUNT = 5000
_OUTPUT = Path(__file__).resolve().parent.parent / "config" / "validation_sites.txt"

# 除外キーワード (ドメイン内に含まれていたら除外)
_EXCLUDE_KEYWORDS: frozenset[str] = frozenset([
    "cdn", "dns", "sdk", "pixel", "beacon", "telemetry", "adserv",
    "tracker", "tracking", "analytics",
])

_EXCLUDE_PATTERNS: list[re.Pattern[str]] = [
    re.compile(p)
    for p in [
        # Google 系 (TLD .google も含む)
        r"google",
        r"gmail\.",
        r"gstatic\.",
        r"googleapis\.",
        r"youtube\.",
        r"youtu\.be$",
        r"ggpht\.",
        r"blogspot\.",
        r"gvt\d",
        # GitHub 系
        r"github",
        r"githubusercontent\.",
        # Yahoo 系
        r"yahoo",
        r"yimg\.",
        # IR BANK / EDINET
        r"irbank\.",
        r"edinet",
        # CDN・インフラ
        r"cloudfront\.net$",
        r"akamai",
        r"cloudflare",
        r"fastly\.",
        r"jsdelivr\.",
        r"unpkg\.",
        r"polyfill\.",
        r"gtld-servers\.",
        r"root-servers\.",
        r"workers\.dev$",
        # 広告・トラッキング
        r"doubleclick\.",
        r"adsense\.",
        r"adnxs\.",
        r"outbrain\.",
        r"taboola\.",
        r"criteo\.",
        r"rubiconproject\.",
        r"pubmatic\.",
        r"casalemedia\.",
        r"scorecardresearch\.",
        r"hotjar\.",
        r"optimizely\.",
        r"newrelic\.",
        r"sentry\.",
        r"datadoghq\.",
        r"nr-data\.",
        r"smartadserver\.",
        r"appflyer",
        r"appsflyer",
        r"app-analytics",
        r"adjust\.",
        r"branch\.io$",
        # SNS CDN
        r"fbcdn\.",
        r"fbsbx\.",
        r"tfbnw\.",
        r"cdninstagram\.",
        r"twimg\.",
        r"tiktok(cdn|v)",
        r"musical\.ly$",
        r"sc-cdn\.",
        r"snapchat\.",
        r"bytepluscdn\.",
        r"bytefcdn",
        r"douyincdn\.",
        # メッセージング
        r"whatsapp\.",
        r"telegram\.",
        # Apple 系 インフラ
        r"icloud\.",
        r"mzstatic\.",
        r"apple-dns\.",
        # Microsoft 系 インフラ
        r"microsoftonline\.",
        r"msedge\.net$",
        r"msftconnecttest\.",
        r"windowsupdate\.",
        r"azure",
        r"office365\.",
        r"office\.com$",
        r"office\.net$",
        r"sharepoint\.",
        r"onedrive\.",
        r"live\.com$",
        r"live\.net$",
        r"skype\.",
        r"trafficmanager\.net$",
        # Amazon 系 インフラ
        r"amazonaws\.",
        r"awsstatic\.",
        r"awsdns",
        r"amazonvideo\.",
        r"media-amazon\.",
        # その他インフラ
        r"domaincontrol\.",
        r"parking\.",
        r"keenetic\.",
        r"in-addr\.arpa$",
        r"nominetdns\.",
    ]
]


def _should_exclude(domain: str) -> bool:
    if any(kw in domain for kw in _EXCLUDE_KEYWORDS):
        return True
    return any(pat.search(domain) for pat in _EXCLUDE_PATTERNS)


def main() -> None:
    print(f"Downloading Tranco top 1M list ...", flush=True)
    resp = requests.get(_TRANCO_URL, timeout=60)
    resp.raise_for_status()

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        csv_name = zf.namelist()[0]
        raw = zf.read(csv_name).decode()

    domains: list[str] = []
    reader = csv.reader(io.StringIO(raw))
    for _rank, domain in reader:
        if _should_exclude(domain):
            continue
        domains.append(domain)
        if len(domains) >= _TARGET_COUNT:
            break

    _OUTPUT.write_text("\n".join(domains) + "\n")
    print(f"Wrote {len(domains)} domains to {_OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
