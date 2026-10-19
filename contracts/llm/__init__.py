"""The language-model providers, as every part of the platform knows them.

The catalog of providers and the models each serves (``providers.json``
and ``models.json``, read through ``providers.py`` and written from
models.dev by ``generate.py``), read by the Data Core, the AI Runtime
and the page alike; and the sign-ins a provider needs beyond a key
(``vertex.py``), which both the Data Core, checking a connection before
it is saved, and the AI Runtime, calling the model, make.
"""
