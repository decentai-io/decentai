# Administration

Who exists, and what each of them may do. The **Admin** section is for those who hold the actions to manage it.

## The model

An identity is never a bag of permissions. It is a chain, walked on every request:

```
user → groups → roles → policies → statements
```

A **policy** is a list of statements, each allowing named actions. A **role** bundles policies. A **group** carries roles. A **user** belongs to groups. Nothing about authority is copied into a session, so a change takes effect on the next request, not the next sign-in.

Two rules hold everywhere:

- **An action is an endpoint.** Every operation the platform exposes is named by itself — `ai:chat:list`, `data:record:create`, `agents:agent:install` — and that name is what a policy grants. There is no separate permission vocabulary to keep in step.
- **Nothing is permitted by default.** A request is refused unless some statement reachable from the caller allows it.

## Organizations

One deployment hosts many organizations, and they cannot see each other. Every user, group, role, policy, credential, record and file belongs to exactly one, and every query is scoped by it independently.

A fresh organization is seeded with two chains: **Administrators**, whose role holds a policy allowing every action, and **Everyone**, the group every user is in implicitly, whose role holds the **baseline** — what any member may do without anyone deciding: use chats, read their own activity and audit trail, keep memory, manage their own API keys, read the organization's skills.

## People

Invite by email on **Admin → Users**; the invitation link lets the person set a password. On your own computer no email is sent: **Add person** shows you a temporary password to hand over, and the person chooses their own at their first sign-in. **Reset password** on a person's row does the same for one who forgot theirs.

A new person is offered the **Members** group already ticked. It grants everyday use — chats, files, saved data, their own credentials — and nothing of administration. A person in no group at all can sign in and do almost nothing. Which agents Members may call is decided on each agent; on your own computer an agent is granted to Members as you install it. Two invariants are enforced: an organization cannot be left with nobody able to manage access, and nobody can grant an action they do not hold themselves.

**Disabling** a person ends their sessions, revokes their API keys, closes their live chats and pauses every schedule they own, keeping all their data. Re-enabling brings the person back; their schedules stay paused until they resume the ones they still want.

**Removing** a person is a hand-over. The dialog shows what they own: their chats, schedules, memories and MCP servers, which are deleted, and their credentials, records, files, skills, model connections and agent sources, which pass to a successor you choose — yourself by default. Whatever they had shared stays shared; agents granted their credentials keep working.

## Groups, roles, policies

Build from the bottom: write a policy naming actions from the catalog, put it in a role, put the role in a group, put people in the group. The catalog on the policy page lists every action with a sentence saying what it is.

Actions worth knowing when writing a policy:

| Action | Grants |
|---|---|
| `agents:agent:install` | Adding sources and installing agents |
| `agents:agent:grant` | Making an installed agent usable by people |
| `secrets:secret:set_owner_any` | Sharing credentials organization-wide and maintaining others' |
| `data:record:set_owner_any` | The same for records |
| `ai:audit:list_all` | Reading the organization's whole audit trail |
| `settings:llm:create` | Adding language-model connections |
| `skills:skill:create` | Writing skills for everyone |

## Sharing

Every stored thing — a credential, a record, a file, a skill, a model connection — follows one rule: private to its creator until shared; shareable with groups the sharer is in, with people they share a group with, and organization-wide only with the escape grant for that domain; editable only by its creator unless that same grant is held. Seeing something is never authority over it. Its creator, or a holder of that grant, may **hand it over** to another member, who becomes its owner with the sharing untouched.

## Model providers

**Settings → Model providers** holds the providers the organization has a key for. Adding one is choosing the provider and pasting its key: the address is filled in, and the provider is asked whether the key works before it is saved. One key serves every model the provider has — the models a chat thinks with, and the ones that embed and write speech down. A model on your own computer or network is added as *Your own server*, by its address.

One provider is the default for every chat that did not choose; a person's last pick is what their next chat starts with; a chat may choose any model of any provider its person can see. Keys are write-only.

**Settings → Chat configuration** chooses two more models, each a provider shared with the whole organization and one of its models: the one that writes spoken messages down (without it the composer has no microphone), and the embedding model agent routing finds the right agent with.

## Connected apps

**Settings → Connected apps** holds what the organization registered with providers — Google, Microsoft, Slack — so members can connect their accounts with a click. One registration per provider: create a *web application* OAuth client in the provider's console with the redirect URI the page shows, and paste the client id and secret here. The registration also records where the provider is: its consent, token and account addresses. The page fills them in from what the installed agents name; where agents name different addresses you choose which ones the registration uses, and where no installed agent names the provider you type them. An agent that names other addresses cannot connect through the registration, and a registration saved without addresses has to be opened and saved again before anyone can connect through it. The provider is chosen, not typed: the page offers first the ids installed agents declare — with the agents that need them — then the known ids by convention (`google`, `microsoft`, `slack`, `github`, `gitlab`, `atlassian`, `salesforce`, `hubspot`, `zoom`, `notion`, `dropbox`, `box`), and a free box for any other id an agent's manifest spells. The secret is write-only. Members never see it; when an agent needs that provider, their Connect button uses it. Removing a registration stops new connections; accounts already connected keep working until their tokens expire, then ask to be reconnected.

On your own computer the form asks for less. An app registered for a person's computer is given no secret by some providers, Microsoft and Dropbox among them, and for those the page asks for the client id alone and says what to choose in the provider's console. Where a provider hands every app a secret, as Google does, the page asks for both. For a provider the page does not know, the secret is optional: leave it empty if the provider gave none.

## Safety

**Settings → Safety** says what agents may do without asking. Five rows, and each starts where the platform stood before the setting existed: no site blocked, every script and program asked about, any package a card names, MCP servers allowed.

- **Sites no agent may open.** A name on the list is refused to every agent, the Browser included, with every address under it.
- **A script in a page.** Asked every time, or once for a site in a chat.
- **A program.** Asked every time; not asked again for a correction that needs nothing new; or asked only when the program reaches a site or uses a credential.
- **Packages a program may install.** Any the card names, or only the ones on your list.
- **MCP servers.** People may add them for their own chats, or nobody can add one and no chat can call one already added.

A looser choice applies only to code the assistant read and found to do what it says: code that does more, or that it could not read, is always shown. What ran without a card is said in the chat with its code, and the **Audit** trail says the setting allowed it.

The trail also says where each call connected: an entry for a function that ran names the sites its agent reached, and how many more. The platform sees names, never what was sent.

## Operating

The platform is a backend, an AI runtime and a web app over a database, configured entirely by environment: one image runs locally, in a container, or in a cloud. The deployment guide in the repository covers first-run seeding, credentials for the runtime's delegation, and the environment variables that name where things live.
