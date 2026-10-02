"""Make the deployment's VAPID key pair — what lets this backend send
web push notifications to browsers that subscribed.

Run once per deployment and put the three lines it prints into the
backend's environment (config.env locally, deploy.env on a server).
The private key is a secret: treat it like the signing key.

    python bootstrap/generate_vapid_keys.py
"""

from __future__ import annotations

import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def main() -> int:
    private = ec.generate_private_key(ec.SECP256R1())
    public = private.public_key()

    def b64url(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    private_raw = private.private_numbers().private_value.to_bytes(32, "big")
    public_raw = public.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    print(f"VAPID_PUBLIC_KEY={b64url(public_raw)}")
    print(f"VAPID_PRIVATE_KEY={b64url(private_raw)}")
    print("VAPID_SUBJECT=mailto:admin@example.com")
    return 0


if __name__ == "__main__":
    sys.exit(main())
