"""Make the deployment's VAPID key pair, which lets the API server push to browsers that subscribed.

Run once per deployment, from the repository's root, in the API server's image, and put the three lines it prints
into deploy/config/deploy.env. The private key is a secret.

    docker run --rm -v "$PWD/deploy/setup:/setup:ro" ghcr.io/decentai-io/decentai-api-server:local \
        python /setup/generate_vapid_keys.py
"""

import base64
import sys

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


class VapidKeys:
    @staticmethod
    def encoded(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")

    @classmethod
    def main(cls) -> int:
        private = ec.generate_private_key(ec.SECP256R1())
        public_raw = private.public_key().public_bytes(serialization.Encoding.X962,
                                                       serialization.PublicFormat.UncompressedPoint)
        print(f"AI_API_VAPID_PUBLIC_KEY={cls.encoded(public_raw)}")
        print(f"AI_API_VAPID_PRIVATE_KEY={cls.encoded(private.private_numbers().private_value.to_bytes(32, 'big'))}")
        print("AI_API_VAPID_SUBJECT=mailto:admin@example.com")
        return 0


if __name__ == "__main__":
    sys.exit(VapidKeys.main())
