"""Generate the backend-service RSA key pair as env-ready lines.

Prints the two halves as single-line \\n-escaped values: paste the private
line into backend/config.env and the public line into ai_runtime/config.env.
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

    print("# backend/config.env")
    print(f"BACKEND_SERVICE_PRIVATE_KEY={escaped(private_pem)}")
    print()
    print("# ai_runtime/config.env")
    print(f"BACKEND_SERVICE_PUBLIC_KEY={escaped(public_pem)}")


if __name__ == "__main__":
    main()
