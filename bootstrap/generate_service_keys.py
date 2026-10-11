"""Generate the two service RSA key pairs as env-ready lines.

One pair is the backend's identity toward the runtime, the other the
runtime's identity toward the backend. Each half is printed as a
single-line \\n-escaped value: the private halves go where they are
named, the public halves to the other side. Nothing is written to disk.
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
    backend_private, backend_public = pair()
    runtime_private, runtime_public = pair()

    print("# backend/config.env")
    print(f"BACKEND_SERVICE_PRIVATE_KEY={backend_private}")
    print(f"AI_RUNTIME_SERVICE_PUBLIC_KEY={runtime_public}")
    print()
    print("# ai_runtime/config.env")
    print(f"BACKEND_SERVICE_PUBLIC_KEY={backend_public}")
    print(f"AI_RUNTIME_SERVICE_PRIVATE_KEY={runtime_private}")


if __name__ == "__main__":
    main()
