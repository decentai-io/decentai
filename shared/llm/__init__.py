"""The language-model providers, as every part of the platform knows them.

The catalog of providers and the models each serves (``providers.json``
and ``models.json``, read through ``providers.py`` and written from
models.dev by ``tools/generate_llm_catalog.py``), read by the API server, the runtime
and the page alike; and the sign-ins a provider needs beyond a key
(``vertex.py``), which both the API server, checking a connection before
it is saved, and the runtime, calling the model, make.
"""
