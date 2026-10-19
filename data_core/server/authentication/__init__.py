"""Authentication + authorization: user → groups → roles → policies.

Many organizations per deployment, each sealed from the others; people
arrive by invitation rather than signing themselves up. Organizations
themselves are created and disabled from outside the application, with
``bootstrap/organizations.py``.

Import directly from each file (this init stays empty to avoid an import
cycle via the stores):

    catalog.py      the access vocabulary: baseline, actions, the fence
    access.py       AccessController — request → user + the gateway check
    credentials.py  PasswordHasher · TokenController (HS256)
    policy.py       ActionCatalog (action = endpoint) · PolicyEngine
    flows.py        AuthController — login, invitations, resets, me
    mail.py         Emails · Mailer (SMTP, or log locally)
"""
