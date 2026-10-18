"""The install's own issuer of node credentials: what a secret manager
or a cluster's workload identity is for a larger deployment
(docs/system/execution-nodes.md).

    python bootstrap/node_issuer.py            sign and write, for ever
    python bootstrap/node_issuer.py --once     sign and write once

It holds the authority's private key (``NODE_AUTHORITY_PRIVATE_KEY``,
made once by bootstrap/setup.py), signs a credential naming the bundled
node (``NODE_NAME``) that lives ten minutes, and writes it where the
node reads it (``NODE_CREDENTIAL_FILE``) — again every few minutes, well
before the last one lapses. The node mounts that folder read-only and
never sees the key: a node that is taken over can sign nothing.

It imports nothing of the Data Core's but the shared contract, and
reaches no network.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from contracts.node import (  # noqa: E402
    CREDENTIAL_SECONDS, key_fingerprint, mint, public_half)

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO").upper(),
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
logger = logging.getLogger("node_issuer")


class Issuer:
    #: How often a fresh credential is written: a third of its life, so
    #: two writes can be missed before the node holds a lapsed one.
    EVERY_SECONDS = CREDENTIAL_SECONDS // 3

    def __init__(self, private_pem: str, node: str, path: Path):
        self.private_pem = private_pem.replace("\\n", "\n")
        self.node = node
        self.path = Path(path)
        # The credential names the key by its fingerprint, which is how
        # the Data Core finds the authority among those it holds.
        self.key_id = key_fingerprint(public_half(self.private_pem))

    def write(self) -> None:
        token = mint(self.private_pem, self.node, key_id=self.key_id,
                     seconds=CREDENTIAL_SECONDS)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        staging = self.path.with_name(f".{self.path.name}.incoming")
        staging.write_text(token + "\n", encoding="utf-8")
        os.chmod(staging, 0o644)
        # In one move: the node never reads a credential half written.
        staging.replace(self.path)
        logger.info(f"A credential for the node '{self.node}' is written "
                    f"({CREDENTIAL_SECONDS // 60} minutes).")

    def run(self) -> None:
        while True:
            try:
                self.write()
            except Exception as exc:  # the next round tries again
                logger.error(f"The node's credential was not written: {exc}")
            time.sleep(self.EVERY_SECONDS)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="node_issuer")
    parser.add_argument("--once", action="store_true",
                        help="write one credential and stop")
    asked = parser.parse_args(argv)
    key = (os.getenv("NODE_AUTHORITY_PRIVATE_KEY") or "").strip()
    if not key:
        print("NODE_AUTHORITY_PRIVATE_KEY is not set: there is no authority "
              "to sign the node's credential with (bootstrap/setup.py makes "
              "one).", file=sys.stderr)
        return 1
    issuer = Issuer(
        key, (os.getenv("NODE_NAME") or "bundled").strip() or "bundled",
        Path(os.getenv("NODE_CREDENTIAL_FILE")
             or "/run/decentai/node/credential"))
    if asked.once:
        issuer.write()
        return 0
    issuer.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
