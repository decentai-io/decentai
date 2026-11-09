"""The issuer service: certifies every node of a deployment (documentation/communication.md).

    __main__.py      Settings, and Issuer: the process, started as python -m issuer
    authority.py     its intermediate, and its own certificate
    controllers.py   what it answers on port 8004, and who may call what
    store.py         its records, in a MongoDB database of its own
"""
