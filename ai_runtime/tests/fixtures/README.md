# Test fixtures

`agents/notebook` is a copy of the Notebook agent published in
the `decentai-agents` repository, kept here because the agents
layer needs something real to load, execute and enforce against — and a
suite that fetched it over the network would fail for reasons that have
nothing to do with the code under test.

They are fixtures, not shipped agents: nothing outside `ai_runtime/tests`
reads this directory, and no deployment installs from it. When the
published agent changes in a way these tests should follow, copy it again.

`agents/llm` is the LLM agent — the reference for `llm: true` functions,
which receive the chat's configured model as a mediated `call.llm` and
hold no credentials of their own. It exists only here; the published
agents that think with the chat's model declare the same flag.

`agents/screen` is the smallest agent that shows a screen the person can
watch and take over (`call.screen`): the road for frames and input, both
ways, proven without a browser.

The three are installed as real agents by `fixture_agents.py`, and share
one cached environment, `ai_runtime/tests/.workerenv`.

`note_manifest.yaml` is the manifest of Note, the example agent
(`decentai-agents`, `note/`), which declares every feature the platform
enforces; `test_contract_edges.py` reads it so the contract's tests need
no other repository.
