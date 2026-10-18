"""Generate a node authority's key pair as env-ready lines.

An authority vouches for execution nodes: a credential its private key
signs admits the node it names, once the public key is registered with
the Data Core (docs/system/execution-nodes.md). One computer's install
makes one, registers it as a shared authority as the seeder starts
(bootstrap/init_db.py), and signs the bundled node's credentials with
it (bootstrap/node_issuer.py) — so a fresh install needs nothing from
the person, and the node itself never holds the private key.

For an authority of your own (an organization's nodes, or nodes behind
your own issuer), run this, keep the private half where your issuer
runs, and register the public half on the Nodes:Authority:Create door.
Nothing is written to disk.
"""

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def escaped(pem: bytes) -> str:
    return pem.decode().strip().replace("\n", "\\n")


def main() -> None:
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
    print("# where the authority signs credentials (the install's issuer)")
    print(f"NODE_AUTHORITY_PRIVATE_KEY={escaped(private_pem)}")
    print()
    print("# registered with the Data Core as an authority")
    print(f"NODE_AUTHORITY_PUBLIC_KEY={escaped(public_pem)}")


if __name__ == "__main__":
    main()
