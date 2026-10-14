# Appendix: the words, and where each part lives

## The words

The paper uses plain words, and several of them stand for one exact
thing in the code. Each is here once. The full list, with every word
the documentation uses, is in [Concepts](../system/concepts.md#the-words).

| Word | What it is | In the code |
|---|---|---|
| **action** | one operation the backend performs, and the name a permission grants: `Domain:Controller:action` | `ACTION_CATALOG`, `backend/server/authentication/catalog.py` |
| **agent** | an approved program: a manifest and the code that does what it declares | `ai_runtime/agents/library.py` |
| **approval** | two things: an administrator approving an agent (an install), and a person's yes to one call above the chat's trust level (a card) | `agent_manifests`; `ai_approvals` |
| **audit trail** | the record of what was done, which the platform only adds to | `backend/database/stores/audit.py` |
| **backend** | the part that owns identity, permissions, the database and the keys | `backend/` |
| **beat** | one turn of the assistant's cycle: one call to the model, one action | `Assistant._beat`, `ai_runtime/mind/assistant.py` |
| **builder** | the confined user that downloads and builds an agent's packages | `ai_runtime/agents_container/places.py` |
| **card** | something in a chat that waits for a person: an approval, a question, code to allow, a sign-in | `ai_runtime/chat/cards.py` |
| **the clock** | what fires schedules and ends sleeps | `ai_runtime/chat/clock.py` |
| **contract** | the manifest (an agent's contract with the platform), or the chat's contract (what this chat may do, answered by the backend) | `contracts/agent_manifest.py`; `AI:Chat:Contract` |
| **delegation** | the short-lived credential the backend makes for one chat, under which the runtime acts for the person | `backend/api/services/chat_session/identity/delegation.py` |
| **executor** | what stands between the assistant and every agent function: the gates a call passes | `ai_runtime/execution/executor.py` |
| **fence** | what a worker may open, enforced by the kernel (Landlock); or what a delegation may call | `ai_runtime/agents_container/spawn_helper.c`; `RUNTIME_ENDPOINTS` |
| **grant** | who may use an agent, which credential an agent may borrow, or what a policy allows | `agent_grants`, `agent_secret_grants`, `policies` |
| **manifest** | an agent's declaration of what it can do, keep and reach | `manifest.yaml`; `contracts/agent_manifest.py` |
| **the model** | the language model the assistant thinks with; it proposes and holds nothing | `ai_runtime/mind/` |
| **permission level** | 0 to 3 on a function: a read, an ordinary change, a wider change, an action that leaves the platform | `permission_level` |
| **the proxy** | the only way from a worker to the internet | `ai_runtime/agents/egress.py` |
| **runtime** | the part that runs the assistant and gates every agent call | `ai_runtime/` |
| **slot** | one resource of one installed agent, and the name its things are kept under: `agt_<ref>__<resource id>` | `resource_id` |
| **spawn helper** | the small program that starts a worker as its agent's own user | `ai_runtime/agents_container/spawn_helper.c` |
| **spawner** | the platform's one process in the agents' container: it starts, builds, lists and ends when the runtime asks | `ai_runtime/agents_container/service.py` |
| **storage reference** | the name of a stored result, which a later call can take as an input instead of the data | `storage_ref` |
| **trace** | what the assistant actually ran in this chat; evidence is checked against it | `state.trace` |
| **trust level** | 0 to 3 on a chat, chosen by the person: a function above it asks first | `backend/api/services/chat_session/settings/trust.py` |
| **valve** | the limit on how many beats one message may take | `max_beats` |
| **worker** | the process one approved agent's code runs in | `decentai_sdk/worker.py` |

## Where each part lives

| Part | Where | The page that describes it |
|---|---|---|
| the gateway and permissions | `backend/api/api_router.py`, `backend/server/authentication/` | [identity](../system/identity.md) |
| stored things and encryption | `backend/api/services/data_layer/`, `backend/database/crypto.py` | [the data layer](../system/data-layer.md) |
| a chat's two sockets | `backend/api/services/chat_session/relay.py`, `ai_runtime/door/` | [the chat session](../system/chat-session.md) |
| the assistant | `ai_runtime/mind/`, prompts in `ai_runtime/prompts/` | [the assistant](../system/assistant.md) |
| the executor and its gates | `ai_runtime/execution/` | [the assistant](../system/assistant.md) |
| agents, packages, workers | `ai_runtime/agents/`, `ai_runtime/door/roster.py` | [agent code](../system/agent-code.md) |
| the agents' container | `ai_runtime/agents_container/` | [the sandbox](../system/sandbox.md) |
| the manifest's grammar | `contracts/agent_manifest.py` | [the manifest](../agents/manifest.md) |
| what an agent's code is given | `decentai_sdk/` | [the SDK](../agents/sdk.md) |
| how the containers are started | `docker-compose.yml`, `ai_runtime/start.sh` | [architecture](../system/architecture.md) |
| the tests | `ai_runtime/tests/`, `backend/tests/`, `tests/`, `examples/tests/` | [local development](../contributing/local-development.md) |
