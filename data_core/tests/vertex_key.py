"""A service account's JSON key for tests, as Google Cloud gives one,
with a private key made here: nothing is ever signed in with it."""

import json


def service_account_key(project="vertex-proj"):
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    pem = rsa.generate_private_key(public_exponent=65537, key_size=2048) \
        .private_bytes(serialization.Encoding.PEM,
                       serialization.PrivateFormat.PKCS8,
                       serialization.NoEncryption()).decode("ascii")
    return json.dumps({
        "type": "service_account", "project_id": project,
        "private_key_id": "k1", "private_key": pem,
        "client_email": f"agent@{project}.iam.gserviceaccount.com",
        "token_uri": "https://oauth2.googleapis.com/token"})
