"""Generate the Data Core's own RSA key pair as env-ready lines.

The Data Core signs everything it tells an execution node with the
private half; nodes are configured with the public half, and believe
nothing it did not sign (docs/system/execution-nodes.md). Each half is
printed as a single-line \\n-escaped value. Nothing is written to disk.

A node's own identity is not made here: nodes are admitted by
credentials an authority signs (bootstrap/generate_node_authority.py).
"""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def escaped(pem: bytes) -> str:
    return pem.decode().strip().replace("\n", "\\n")


def pair():
    private = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    private_pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    public_pem = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return escaped(private_pem), escaped(public_pem)


def main() -> None:
    data_core_private, data_core_public = pair()

    print("# the Data Core (data_core/config.env)")
    print(f"DATA_CORE_SERVICE_PRIVATE_KEY={data_core_private}")
    print()
    print("# every execution node (ai_runtime/config.env)")
    print(f"DATA_CORE_SERVICE_PUBLIC_KEY={data_core_public}")


if __name__ == "__main__":
    main()
