#!/usr/bin/env python3
"""Summarize claimed search-crawler traffic from privacy-minimized nginx logs."""

from __future__ import annotations

import argparse
import gzip
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable, TextIO


CRAWLERS = {
    "googlebot": re.compile(r"googlebot", re.IGNORECASE),
    "bingbot": re.compile(r"bingbot", re.IGNORECASE),
    "yandexbot": re.compile(r"yandex(?:bot|images)", re.IGNORECASE),
}
WASTE_PATTERNS = (
    re.compile(r"/page/1/?$"),
    re.compile(r"/index\.html$"),
    re.compile(r"//"),
    re.compile(r"^/en/?$"),
)


def _open_log(path: Path) -> TextIO:
    if path.suffix == ".gz":
        return gzip.open(path, mode="rt", encoding="utf-8", errors="replace")
    return path.open(encoding="utf-8", errors="replace")


def _entries(paths: Iterable[Path]) -> Iterable[dict[str, object]]:
    for path in paths:
        with _open_log(path) as source:
            for line in source:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict):
                    yield entry


def _crawler_name(user_agent: str) -> str | None:
    for name, pattern in CRAWLERS.items():
        if pattern.search(user_agent):
            return name
    return None


def build_report(
    paths: Iterable[Path],
    *,
    canonical_host: str,
    since: datetime,
) -> dict[str, object]:
    status_by_crawler: dict[str, Counter[str]] = defaultdict(Counter)
    paths_by_crawler: dict[str, Counter[str]] = defaultdict(Counter)
    noncanonical_hosts: Counter[str] = Counter()
    waste_paths: Counter[str] = Counter()
    query_requests: Counter[str] = Counter()
    total = 0

    for entry in _entries(paths):
        user_agent = str(entry.get("user_agent", ""))
        crawler = _crawler_name(user_agent)
        if crawler is None:
            continue

        try:
            observed_at = datetime.fromisoformat(str(entry["time"]))
        except (KeyError, TypeError, ValueError):
            continue
        if observed_at.tzinfo is None:
            observed_at = observed_at.replace(tzinfo=timezone.utc)
        if observed_at < since:
            continue

        total += 1
        status = str(entry.get("status", "unknown"))
        uri = str(entry.get("uri", ""))
        host = str(entry.get("host", ""))
        status_by_crawler[crawler][status] += 1
        paths_by_crawler[crawler][uri] += 1
        if host != canonical_host:
            noncanonical_hosts[host or "<empty>"] += 1
        if any(pattern.search(uri) for pattern in WASTE_PATTERNS):
            waste_paths[uri] += 1
        if str(entry.get("has_args", "")) == "?":
            query_requests[uri] += 1

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since.isoformat(),
        "scope": "claimed crawler user-agents; IP ownership is not asserted",
        "total_requests": total,
        "status_by_crawler": {
            crawler: dict(sorted(counts.items()))
            for crawler, counts in sorted(status_by_crawler.items())
        },
        "top_paths_by_crawler": {
            crawler: counts.most_common(25)
            for crawler, counts in sorted(paths_by_crawler.items())
        },
        "noncanonical_hosts": noncanonical_hosts.most_common(25),
        "crawl_waste_paths": waste_paths.most_common(25),
        "query_string_requests": query_requests.most_common(25),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--log-glob",
        default="/var/log/nginx/mutating.tech.access.json.log*",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("/var/log/nginx/mutating.tech.crawler-report.json"),
    )
    parser.add_argument("--canonical-host", default="mutating.tech")
    parser.add_argument("--hours", type=int, default=24)
    args = parser.parse_args()

    if args.hours <= 0:
        parser.error("--hours must be positive")

    paths = sorted(Path("/").glob(args.log_glob.lstrip("/")))
    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    report = build_report(paths, canonical_host=args.canonical_host, since=since)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temporary = args.output.with_suffix(f"{args.output.suffix}.tmp")
    temporary.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.replace(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
