"""install_agents: every agent its catalogs offer, installed in one go, for an organization's administrator.

Each catalog is saved as an agent source, or read again where it is saved already; then every agent it offers is
installed that is not yet, or has a newer version. What a catalog marks in error is left, and said. It acts through
the API server as the person whose API key it is given (the web client, Settings, API keys): an administrator.

    python tools/install_agents.py
        the official catalogs, on the API server at http://localhost:4280; the key is asked for
    python tools/install_agents.py --api https://decentai.example.com --source https://agents.example.com/catalog.json
        another server, and catalogs of one's own (--source again for each)

The key is asked for and never shown, or read from DECENTAI_API_KEY.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Callable

#: The catalogs the project publishes.
OFFICIAL = (
    "https://raw.githubusercontent.com/decentai-io/decentai-official-agents/main/catalog/catalog.json",
    "https://raw.githubusercontent.com/decentai-io/decentai-browser-agent/main/catalog/catalog.json",
)


class Refused(Exception):
    """A call the API server refused, in its words."""


class Api:
    """`POST /app` as one person, waiting out a rate limit (429) the way the server asks."""

    TRIES = 6

    def __init__(self, base: str, key: str):
        self.base, self.key = base.rstrip("/"), key

    def call(self, endpoint: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        body = json.dumps({"endpoint": endpoint, "data": data or {}}).encode()
        for _ in range(self.TRIES):
            request = urllib.request.Request(f"{self.base}/app", body, method="POST", headers={
                "Content-Type": "application/json", "Authorization": f"Bearer {self.key}"})
            try:
                with urllib.request.urlopen(request, timeout=300) as answer:
                    return json.loads(answer.read() or b"{}")
            except urllib.error.HTTPError as failed:
                error = json.loads(failed.read() or b"{}").get("error") or {}
                if failed.code != 429:
                    raise Refused(f"{endpoint}: {error.get('message') or failed.reason}") from None
                time.sleep(float(error.get("retry_after", 10)))
        raise Refused(f"{endpoint}: still too many calls after waiting")


def install_all(api: Api, sources: list[str], say: Callable[[str], None] = print) -> bool:
    """Each source saved or read again, then everything it offers installed; whether nothing failed."""
    saved = {source["url"]: source for source in api.call("Agents:Agent:sources").get("sources", [])}
    for url in sources:
        if url in saved:
            source = api.call("Agents:Agent:sourcerefresh", {"source_id": saved[url]["source_id"]})["source"]
        else:
            source = api.call("Agents:Agent:sourcecreate", {"url": url})["source"]
        say(f"{url}\n  " + (f"its catalog was not read: {source['error']}" if source.get("error")
                            else f"{source.get('name') or 'catalog'}: {source.get('agents', 0)} agents"))

    offers = api.call("Agents:Agent:available").get("agents", [])
    wanted = [offer for offer in offers if offer["status"] in ("not_installed", "update_available")
              and not offer.get("errors")]
    flawed = [offer for offer in offers if offer.get("errors")]
    say(f"{len(offers)} offered: {len(wanted)} to install, "
        f"{sum(offer['status'] == 'installed' for offer in offers)} installed already.")

    failed = 0
    for offer in wanted:
        agent = offer["manifest"]["agent"]
        try:
            api.call("Agents:Agent:install", {"source_id": offer["source_id"], "agent_id": agent["id"],
                                              "version": agent["version"], "digest": offer["digest"]})
            say(f"  installed {agent['id']} {agent['version']}")
        except Refused as refused:
            failed += 1
            say(f"  not installed {agent['id']} {agent['version']}: {refused}")
    for offer in flawed:
        say(f"  left out {offer['manifest']['agent']['id']}: {'; '.join(offer['errors'])[:200]}")
    say(f"{len(wanted) - failed} installed, {failed} failed, {len(flawed)} in error in their catalog.")
    return not failed


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--api", default="http://localhost:4280", help="the API server's address")
    parser.add_argument("--source", action="append", help="a catalog's address; the official ones when none is given")
    args = parser.parse_args()
    key = os.environ.get("DECENTAI_API_KEY") or getpass.getpass("API key (not shown): ").strip()
    if not key:
        print("No API key given.")
        return 1
    try:
        return 0 if install_all(Api(args.api, key), args.source or list(OFFICIAL)) else 1
    except Refused as refused:
        print(refused)
        return 1


if __name__ == "__main__":
    sys.exit(main())
