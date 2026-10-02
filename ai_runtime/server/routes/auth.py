"""Who may open the runtime's doors.

The chat socket answers the backend and nobody else, and this is the
one place that decides what "the backend" means: a bearer token only the backend can sign (RS256 — this process
holds just the public key), with issuer, audience and expiry all
required rather than merely checked when present.
"""

import jwt

def verify_backend_service(token: str, settings):
    """Bearer service token → its claims, or None. Only the backend can
    sign one (RS256 — this process holds just the public key). Key
    rotation is carried by the token's kid header. The runtime access
    token arriving beside it stays opaque here: it is the backend's own
    HS256 secret, held only to be presented back on /app calls."""
    if not token or not settings.backend_service_public_key:
        return None
    try:
        claims = jwt.decode(
            token,
            settings.backend_service_public_key,
            algorithms=["RS256"],
            audience=settings.backend_service_audience,
            issuer=settings.backend_token_issuer,
            options={"require": ["exp", "iat", "nbf", "jti", "sub"]},
        )
    except jwt.PyJWTError:
        return None
    if claims.get("typ") != "backend_service":
        return None
    return claims
