# Actions

Every operation the gateway dispatches, by service. An action is an
endpoint's name — `POST /app` with `endpoint: "Domain:Controller:action"` —
and the name is what a policy grants. **Baseline** actions are held by
every member of an organization without a decision; **runtime** marks
the actions a chat's delegation may call, and nothing else.

Generated from `backend/server/authentication/catalog.py` by
`docs/reference/generate.py`; do not edit by hand.

180 actions in 34 services; 51 baseline.

## Own account

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `account:profile:get` | View your own profile | yes |  |
| `account:profile:update` | Edit your own name | yes |  |
| `account:profile:peers` | List the people who share a group with you | yes |  |

## Organization

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `iam:organization:get` | View organization details |  |  |
| `iam:organization:rename` | Rename the organization |  |  |

## Users

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `iam:user:list` | View users |  |  |
| `iam:user:get` | View one user |  |  |
| `iam:user:update` | Edit a user's profile |  |  |
| `iam:user:set_groups` | Place a user into groups |  |  |
| `iam:user:set_status` | Disable or re-enable a user |  |  |
| `iam:user:delete` | Remove a user, handing what they own to a successor |  |  |
| `iam:user:leaving` | Preview what a user owns — chats, credentials, records, files, skills, connections, sources — before removing them |  |  |

## Invitations

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `iam:invitation:list` | View pending invitations |  |  |
| `iam:invitation:create` | Invite users |  |  |
| `iam:invitation:revoke` | Revoke a pending invitation |  |  |

## Groups

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `iam:group:list` | View groups |  |  |
| `iam:group:get` | View one group and its members |  |  |
| `iam:group:create` | Create groups |  |  |
| `iam:group:rename` | Rename a group |  |  |
| `iam:group:set_roles` | Attach roles to a group |  |  |
| `iam:group:delete` | Delete a group |  |  |

## Roles

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `iam:role:list` | View roles |  |  |
| `iam:role:get` | View one role |  |  |
| `iam:role:create` | Create roles |  |  |
| `iam:role:update` | Rename a role |  |  |
| `iam:role:set_policies` | Attach policies to a role |  |  |
| `iam:role:delete` | Delete roles |  |  |

## Policies

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `iam:policy:list` | View policies |  |  |
| `iam:policy:get` | View one policy and its permissions |  |  |
| `iam:policy:create` | Create policies |  |  |
| `iam:policy:update` | Edit a policy |  |  |
| `iam:policy:delete` | Delete policies |  |  |

## Logins asked for by agents

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `secrets:credential:save` | Type in a login an agent asks for as it works, saved as one of your secrets | yes |  |
| `secrets:credential:allow` | Let an agent use one of your saved logins on a site | yes |  |
| `secrets:credential:resolve` | Let your chat's engine use the logins you saved for the sites its agents work on |  | yes |

## Secrets

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `secrets:secret:list` | View the secrets shared with you |  |  |
| `secrets:secret:get` | View one secret's details |  |  |
| `secrets:secret:create` | Create secrets |  |  |
| `secrets:secret:update` | Edit a secret you created |  |  |
| `secrets:secret:delete` | Delete a secret you created |  |  |
| `secrets:secret:set_default` | Choose which of your credentials answers by default | yes |  |
| `secrets:secret:use` | Let your chat's engine use a credential you can see — the model key a chat needs to start, and the credentials its agents were given |  | yes |
| `secrets:secret:transfer` | Hand one of your credentials to another member | yes |  |
| `secrets:secret:instances` | Let your chat's engine list which of your credentials an agent may use, by name |  | yes |
| `secrets:secret:set_owner_any` | Share secrets organization-wide or with any group or user, and maintain secrets created by others |  |  |

## Connected accounts

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `secrets:oauth:start` | Connect an account by signing in with the provider, instead of typing a credential | yes |  |

## Secret types

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `secrets:definition:list` | View the secret types |  |  |
| `secrets:definition:get` | View one secret type | yes |  |

## Notifications

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:notifications:get` | See how you are reached when a chat needs you | yes |  |
| `settings:notifications:subscribe` | Let this device receive notifications | yes |  |
| `settings:notifications:unsubscribe` | Stop notifications on a device | yes |  |
| `settings:notifications:update` | Choose whether an email may follow | yes |  |
| `settings:notifications:test` | Send yourself a test notification | yes |  |

## Speech to text

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:speech:get` | See which model writes spoken messages down |  |  |
| `settings:speech:update` | Choose the transcription model |  |  |
| `settings:speech:transcribe` | Speak a message instead of typing it | yes |  |

## Safety

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:safety:get` | See what agents may do without asking |  |  |
| `settings:safety:update` | Choose the sites no agent may open, how often scripts and programs ask, and the packages a program may install |  |  |

## Agent routing

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:routing:get` | See how chats find the right agent among many |  |  |
| `settings:routing:update` | Choose the embedding model and the routing numbers |  |  |

## LLM connections

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:llm:use` | Let your chat's engine read the model key of a connection you can see |  | yes |
| `settings:llm:list` | View the organization's LLM connections | yes |  |
| `settings:llm:providers` | View the language-model providers a connection may name | yes |  |
| `settings:llm:create` | Add an LLM connection |  |  |
| `settings:llm:update` | Edit an LLM connection or rotate its key |  |  |
| `settings:llm:setdefault` | Choose the default LLM connection |  |  |
| `settings:llm:delete` | Remove an LLM connection |  |  |
| `settings:llm:transfer` | Hand one of your LLM connections to another member |  |  |
| `settings:llm:manage_any` | Edit, rotate, remove and transfer any of the organization's LLM connections, not only your own |  |  |

## Agent data

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `data:record:list` | View the agent records shared with you |  | yes |
| `data:record:get` | View one record's details |  | yes |
| `data:record:shapes` | See which record types installed agents declare — the shapes a new record can be created in |  | yes |
| `data:record:create` | Create agent records |  | yes |
| `data:record:update` | Edit a record you created |  | yes |
| `data:record:delete` | Delete a record you created |  | yes |
| `data:record:transfer` | Hand one of your records to another member | yes |  |
| `data:record:set_owner_any` | Share records organization-wide or with any group or user, and maintain records created by others |  |  |

## Memory

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:memory:list` | See what the assistant remembers about you | yes | yes |
| `settings:memory:create` | Let a chat save a memory — the assistant's own write, shown in the conversation as it happens | yes | yes |
| `settings:memory:update` | Correct something the assistant remembers | yes |  |
| `settings:memory:delete` | Delete something the assistant remembers | yes |  |

## Connected apps

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:oauth:list` | See which provider apps the organization registered, and the redirect URI to give a provider |  |  |
| `settings:oauth:create` | Register a provider app (client id and secret) |  |  |
| `settings:oauth:update` | Rotate a provider app's id or secret |  |  |
| `settings:oauth:delete` | Remove a provider app registration |  |  |

## API keys

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `settings:apikey:list` | See your API keys — names and dates, never the secret | yes |  |
| `settings:apikey:create` | Make an API key that acts as you, for scripts | yes |  |
| `settings:apikey:revoke` | Revoke one of your API keys | yes |  |

## Skills

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `skills:skill:list` | View the skills shared with you | yes | yes |
| `skills:skill:get` | Read one skill's full body | yes | yes |
| `skills:skill:create` | Create skills |  |  |
| `skills:skill:update` | Edit a skill you created |  |  |
| `skills:skill:delete` | Delete a skill you created |  |  |
| `skills:skill:transfer` | Hand one of your skills to another member | yes |  |
| `skills:skill:set_owner_any` | Share skills organization-wide or with any group or user, and maintain skills created by others |  |  |

## MCP servers

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `mcp:server:list` | See the MCP servers you added | yes |  |
| `mcp:server:get` | See one of your MCP servers and its tools | yes |  |
| `mcp:server:create` | Add an MCP server for your own chats | yes |  |
| `mcp:server:update` | Switch one of your MCP servers or its tools on or off, and set what each tool costs to call | yes |  |
| `mcp:server:refresh` | Read again what one of your MCP servers offers | yes |  |
| `mcp:server:delete` | Remove one of your MCP servers | yes |  |
| `mcp:server:use` | Your assistant reaches one of your MCP servers | yes | yes |

## Files

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `files:file:list` | View the files shared with you |  | yes |
| `files:file:get` | View one file's details |  | yes |
| `files:file:upload` | Upload files |  | yes |
| `files:file:download` | Download files shared with you |  | yes |
| `files:file:update` | Edit a file you uploaded |  |  |
| `files:file:delete` | Delete a file you uploaded |  | yes |
| `files:file:transfer` | Hand one of your files to another member | yes |  |
| `files:file:set_owner_any` | Share files organization-wide or with any group or user, and maintain files uploaded by others |  |  |

## AI chat

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:chat:create` | Create chats |  |  |
| `ai:chat:open` | Open a chat session — create or resume it with the model, agents and permissions resolved in one answer |  |  |
| `ai:chat:get` | Read owned chats |  |  |
| `ai:chat:list` | List owned chats |  |  |
| `ai:chat:update` | Update owned chats |  |  |
| `ai:chat:title` | Your assistant names a chat from its content |  | yes |
| `ai:chat:archive` | Archive owned chats |  |  |
| `ai:chat:restore` | Restore archived chats |  |  |
| `ai:chat:delete` | Delete owned chats |  |  |
| `ai:chat:sendmessage` | Send chat messages |  |  |
| `ai:chat:stop` | Stop what your assistant is doing |  |  |
| `ai:chat:uploadfile` | Upload files to owned chats |  |  |
| `ai:chat:plan` | Your engine records what a turn set out to do |  | yes |
| `ai:chat:contract` | Your assistant asks what this chat may do — the model, the agents, the grants, the trust level |  | yes |

## AI assistant state

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:state:get` | Your assistant reads its own mind back |  | yes |
| `ai:state:save` | Your assistant persists its mind, every beat |  | yes |

## Activity

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:activity:list` | See your schedules, pending approvals and background work across every chat | yes |  |

## Stop everything

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:switch:status` | See whether you have stopped everything | yes |  |
| `ai:switch:stop` | Stop everything your chats are doing, at once, and hold it stopped | yes |  |
| `ai:switch:resume` | Let your chats run again | yes |  |

## AI approvals

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:approval:list` | See a chat's pending approval cards |  | yes |
| `ai:approval:decide` | Approve or deny what your assistant asked |  |  |
| `ai:approval:open` | Your assistant asks for your approval |  | yes |
| `ai:approval:expire` | Your assistant closes a question nobody answered |  | yes |

## AI schedules

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:schedule:load` | Your assistant loads its reminders and scheduled checks |  | yes |
| `ai:schedule:add` | Your assistant sets a reminder or a scheduled check |  | yes |
| `ai:schedule:ran` | Your assistant records what a reminder or a scheduled check did |  | yes |
| `ai:schedule:remove` | Your assistant takes a reminder or a scheduled check off the clock |  | yes |
| `ai:schedule:functions` | See which of your functions may run on a schedule | yes |  |
| `ai:schedule:create` | Put a reminder or a schedulable function on your own clock, from the page | yes |  |
| `ai:schedule:update` | Pause or resume one of your schedules | yes |  |
| `ai:schedule:delete` | Take a schedule off your clock | yes |  |

## AI messages

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:message:list` | Read a chat's messages |  | yes |
| `ai:message:create` | Your engine records a chat message |  | yes |

## AI events

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:event:list` | Replay a chat's events after a disconnect |  |  |
| `ai:event:append` | Your engine records what it is doing |  | yes |
| `ai:event:record` | Your assistant records what the world told it, before acting on it |  | yes |
| `ai:event:since` | Your assistant reads what it has not yet absorbed |  | yes |

## AI results

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:storage:get` | Read a stored result behind a table or chart |  | yes |
| `ai:storage:create` | Your engine stores a verified result |  | yes |

## Agents

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `agents:agent:list` | See which agents are installed |  |  |
| `agents:agent:fetch_package` | Let your chat's engine fetch the code of an agent your organization approved | yes | yes |
| `agents:agent:pinned_digests` | Let your chat's engine find out which agent code is still approved anywhere, so it can delete the rest | yes | yes |
| `agents:agent:prepared` | Let your chat's engine report that an agent's code is ready to run, so the Agents page can say so | yes | yes |
| `agents:agent:available` | See the agents this deployment could install |  |  |
| `agents:agent:get` | Read an agent's manifest |  |  |
| `agents:agent:install` | Install an agent |  |  |
| `agents:agent:delete` | Remove an installed agent |  |  |
| `agents:agent:sources` | List saved agent repository sources |  |  |
| `agents:agent:sourcecreate` | Save an agent repository source |  |  |
| `agents:agent:sourceupdate` | Edit an agent repository source |  |  |
| `agents:agent:sourcerefresh` | Refresh an agent repository source |  |  |
| `agents:agent:sourcedelete` | Remove an agent repository source |  |  |
| `agents:agent:sourcepurge` | Uninstall every agent installed from a source, then remove the source |  |  |
| `agents:agent:sourcetransfer` | Hand one of your agent sources to another member |  |  |
| `agents:agent:source_manage_any` | Edit, refresh, remove and transfer any of the organization's agent sources, not only your own |  |  |
| `agents:agent:samples` | See what sample data an installed agent ships | yes |  |
| `agents:agent:loadsamples` | Load an installed agent's sample data as your own records | yes |  |
| `agents:agent:removesamples` | Remove the sample data you loaded | yes |  |
| `agents:agent:grants` | See who may use an installed agent |  |  |
| `agents:agent:grant` | Give someone access to an installed agent |  |  |
| `agents:agent:revoke` | Take away access to an installed agent |  |  |
| `agents:agent:secretgrants` | See which credentials an installed agent may use |  |  |
| `agents:agent:secretgrant` | Let an installed agent use one of your credentials |  |  |
| `agents:agent:secretrevoke` | Take a credential away from an installed agent |  |  |
| `agents:agent:secretlendable` | See which other installed agents a connected account could be lent to |  |  |
| `agents:agent:secretlendmany` | Let several installed agents use one of your credentials at once |  |  |

## AI audit trail

| Action | Grants | Baseline | Runtime |
|---|---|---|---|
| `ai:audit:list` | Read the record of what agents did for you | yes |  |
| `ai:audit:list_all` | Read the organization's whole audit trail — everyone's chats, installs, grants and credential reads |  |  |
| `ai:audit:record` | The runtime's own write: every function it ran, its inputs in outline, its outcome and duration |  | yes |

