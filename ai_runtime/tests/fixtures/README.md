# Test fixtures

`agents/notebook` is a copy of the Notebook agent published in
the `decentai-agents` repository, kept here because the agents
layer needs something real to load, execute and enforce against — and a
suite that fetched it over the network would fail for reasons that have
nothing to do with the code under test.

It is a fixture, not a shipped agent: nothing in `ai_runtime` reads this
directory, and no deployment installs from it. When the published agent
changes in a way these tests should follow, copy it again.

`agents/llm` is the LLM agent — the reference for `llm: true` functions,
which receive the chat's configured model as a mediated `call.llm` and
hold no credentials of their own. It is the copy the platform's tests
load; the same package is published for installation from the agents
repository.
