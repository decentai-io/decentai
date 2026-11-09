"""How the parts of DecentAI talk: every node serves HTTPS on port 8004, over mutual TLS.

    identity.py      keys, X.509 certificates, roots, the identity folder
    router.py        which controller answers a call: Domain:Controller:action
    node.py          the server on 8004 (/health, /app), and calls to other nodes
    enrollment.py    a node's first certificate and its renewals, asked for by the node itself
"""
