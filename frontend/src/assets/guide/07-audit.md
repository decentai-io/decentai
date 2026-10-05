# The audit trail

The platform keeps a record of what agents did, and of what people changed. It is written at the moments that matter, by the platform itself — never by the assistant, and never carrying a secret value — and it cannot be edited.

## What is recorded

| Event | When | What it carries |
|---|---|---|
| **Function ran** | every time the runtime invoked an agent's function, whether it succeeded, failed, was refused by the platform, or was denied by you | the agent, the function, its level and the chat's, the inputs in outline, the outcome, how long it took, and the reference of the stored result |
| **Approval requested / answered** | a call cost more than the chat's level | the function, the level, who decided and what |
| **Credential read** | an agent used a credential | which credential, by name |
| **Model key read** | a chat used a language model | which connection |
| **Agent installed / removed**, **granted / revoked**, **source added / changed / refreshed / removed** | an administrator acted | the agent and the version |
| **Sharing changed**, **Safety setting changed**, **person disabled / deleted** | somebody with the right acted | what changed, from what to what |
| **Platform action** | any other change made through the platform — a record edited, a setting saved — and each one that was refused | the action, by whom, and the outcome |

*Inputs in outline* means exactly that: long values are cut, long lists are counted, and the whole is bounded. The trail says what was asked, never copies a document into itself.

## Reading it

- **In a chat**, the *Activity & audit* dialog is that chat's trail as a timeline. Each row opens to its details; a function that stored a result can open the result itself.
- **Settings → Audit → Mine** is your own trail across every chat you own, with search, event filters, a date window, and paging into the past.
- **Settings → Audit → Organization** includes everyone's trail and organization events such as installs, grants, and sources. This view requires administrative access; without it the page shows your own trail and no tabs.

Your own trail is yours by default: reading what agents did for you is not a privilege to hand out.

## What it is for

- To check a claim: the assistant said it sent the reminder; the trail shows the Email function ran at level 3 after your approval, and what it returned.
- To understand a failure: a function that failed carries its error; one the platform refused says why.
- To answer *who did what*: every event names its actor, and an organization's trail is the complete record.
