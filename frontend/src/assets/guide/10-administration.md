# Administration

Who exists, and what each of them may do. It is reached from the menu on your name at the foot of the sidebar: **Admin console** shows your **Profile**, the **Organization** (its name), and **IAM** — Users, Groups, Roles and Policies — to those who hold the actions to manage them, and **Main workspace** in the same menu goes back.

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

A fresh organization is seeded with three chains. **Administrators**: a role holding a policy that allows every action. **Everyone**, the group every user is in implicitly, whose role holds the **baseline** — what any member may do without anyone deciding: their own profile, what the assistant remembers about them, their own API keys, schedules, MCP servers and notifications, their own audit trail, stopping and resuming their own work, reading the organization's skills and its list of model providers, loading an agent's sample data. And **Members**, which grants everyday use. Chatting is Members', not the baseline's.

## People

Invite by email on **Admin console → IAM → Users**; the invitation link lets the person set a password. Where no mail server is set no email is sent, and the page offers **Add person** instead: it shows you a temporary password to hand over, and the person chooses their own at their first sign-in. **Reset password** on a person's row does the same for one who forgot theirs, whether or not email is sent.

A new person is offered the **Members** group already ticked. It grants everyday use — chats, files, saved data, their own credentials — and nothing of administration. A person in no group at all can sign in and do almost nothing. Which agents Members may call is decided on each agent: installing one grants it to whoever installed it and to the groups they are in, and its **Access** is where others are given it. Two invariants are enforced: an organization cannot be left with nobody able to manage access, and nobody can grant an action they do not hold themselves.

**Disabling** a person ends their sessions, revokes their API keys, closes their live chats and pauses every schedule they own, keeping all their data. Re-enabling brings the person back; their schedules stay paused until they resume the ones they still want.

**Removing** a person is a hand-over. The dialog shows what they own: their chats, schedules, memories and MCP servers, which are deleted, and their credentials, records, files, skills, model connections and agent sources, which pass to a successor you choose — yourself by default. Whatever they had shared stays shared; agents granted their credentials keep working.

## Groups, roles, policies

Build from the bottom: write a policy naming actions from the catalog, put it in a role, put the role in a group, put people in the group. The catalog on the policy page lists every action with a sentence saying what it is.

Actions worth knowing when writing a policy:

| Action | Grants |
|---|---|
| `agents:agent:install` | Installing and updating agents |
| `agents:agent:sourcecreate`, `sourceupdate`, `sourcerefresh`, `sourcedelete` | Adding, changing, refreshing and removing the sources agents are installed from |
| `agents:agent:grant` | Making an installed agent usable by people |
| `secrets:secret:set_owner_any` | Sharing credentials organization-wide and maintaining others' |
| `data:record:set_owner_any` | The same for records |
| `ai:audit:list_all` | Reading the organization's whole audit trail |
| `agents:monitor:usage`, `agents:monitor:events`, `agents:monitor:files` | Seeing what agents use and what was seen of them, on **Settings → Monitoring** |
| `settings:llm:create` | Adding language-model connections |
| `skills:skill:create` | Writing skills, private until shared |
| `skills:skill:set_owner_any` | Sharing skills organization-wide and maintaining others' |
| `settings:llm:manage_any`, `agents:agent:source_manage_any` | Seeing and maintaining every model connection, and every agent source, of the organization |

## Sharing

Every stored thing — a credential, a record, a file, a skill, a model connection, an agent source — follows one rule: private to its creator until shared; shareable with groups the sharer is in and with people they share a group with; editable only by its creator unless the domain's escape grant is held. Sharing with the whole organization needs that grant for credentials, records, files and skills, and is open to the creator for model connections and agent sources, which exist to be used. The escape grant for a credential, a record, a file or a skill reaches what its holder can already see; the two for model connections and agent sources reach every one, since an organization depends on them. Seeing something is never authority over it. Its creator, or a holder of that grant, may **hand it over** to another member, who becomes its owner with the sharing untouched.

## Model providers

**Settings → Model providers** holds the providers the organization has a key for. Adding one is choosing the provider and pasting its key: the address is filled in, and the provider is asked whether the key works before it is saved. One key serves every model the provider has — the models a chat thinks with, and the ones that embed and write speech down. A model on your own computer or network is added as *Your own server*, by its address.

A provider is private to whoever added it until they share it, and the first one added is the default. Share one with everyone for others to use it — and for speech and agent routing, which take only a provider shared with everyone. One provider is the default for every chat that did not choose; a person's last pick is what their next chat starts with; a chat may choose any model of any provider its person can see. Keys are write-only.

**Settings → Chat configuration** chooses two more models, each a provider shared with the whole organization and one of its models: the one that writes spoken messages down (without it the composer has no microphone), and the embedding model agent routing finds the right agent with. The same page holds *Your chat defaults* — the model, trust level, turn budget and number of skills a new chat of yours starts with — your notifications, and the numbers agent routing works by.

## Connected apps

**Settings → Connected apps** holds what the organization registered with providers — Google, Microsoft, Slack — so members can connect their accounts with a click. One registration per provider: register an app in the provider's console with the *OAuth redirect URL* the page shows, and paste the client id and, where the provider issued one, the secret here. The registration also records where the provider is: its consent, token and account addresses. The page fills them in from what the installed agents name; where agents name different addresses you choose which ones the registration uses, and where no installed agent names the provider you type them. An agent that names other addresses cannot connect through the registration, and a registration saved without addresses has to be opened and saved again before anyone can connect through it. The provider is chosen, not typed: the page offers first the ids installed agents declare — with the agents that need them — then the known ids by convention (`google`, `microsoft`, `slack`, `github`, `gitlab`, `atlassian`, `salesforce`, `hubspot`, `zoom`, `notion`, `dropbox`, `box`), and a free box for any other id an agent's manifest spells. The secret is write-only. Members never see it; when an agent needs that provider, their Connect button uses it. Removing a registration stops new connections; accounts already connected keep working until their tokens expire, then ask to be reconnected.

A secret is the provider's to issue. An app registered for a server is given one; an app registered for a person's own computer is given none by some providers, Microsoft and Dropbox among them. For those the secret is optional, and the page says what to choose in the provider's console. Where a provider hands every app a secret, as Google does, the page asks for both. For a provider the page does not know, the secret is optional: leave it empty if the provider gave none.

## Safety

**Settings → Safety** says what agents may do without asking. Five rows, and each starts where the platform stood before the setting existed: no site blocked, every script and program asked about, any package a card names, MCP servers allowed.

- **Sites no agent may open.** A name on the list is refused to every agent, the Browser included, with every address under it.
- **A script in a page.** Asked every time, or once for a site in a chat.
- **A program.** Asked every time; not asked again for a correction that needs nothing new; or asked only when the program reaches a site or uses a credential.
- **Packages a program may install.** Any the card names, or only the ones on your list.
- **MCP servers.** People may add them for their own chats, or nobody can add one and no chat can call one already added.

A looser choice applies only to code the assistant read and found to do what it says: code that does more, or that it could not read, is always shown. What ran without a card is said in the chat with its code, and the **Audit** trail says the setting allowed it.

The trail also says where each call connected: an entry for a function that ran names the sites its agent reached, and how many more. The platform sees names, never what was sent.

## Monitoring

**Settings → Monitoring** shows what your organization's agents use and what was seen of them. Administrators see it; anyone else needs the `agents:monitor` actions. Nothing on the page changes anything.

The page has three views: **Agents**, **Events** and **Connections**.

- **Agents** opens with **The platform** on an install that is one organization's: what each part holds now — the backend, the runtime, the agents' container and the database, with what the database keeps on disk. Then the agents: how much memory and how many processors they are given together, how much they hold now, and for each running agent its memory, its share of the processor, how many processes it runs and how much it keeps on disk. **Files** lists what an agent keeps in its own folder. An agent is listed only while it runs: it starts when a chat first calls it.
- **Events.** When each agent started and ended and why, each line it wrote to its log, each process it started, and each job the platform did to it. Choose an agent or a kind to narrow the list.
- **Connections.** Every connection an agent asked for, made or refused, with the site, how much was sent and received, and why one was refused.

What a connection carried is never read: the platform sees a site's name and how many bytes passed. A process that lasted under a second can be missed. An agent's own log is the agent's to write, so read an agent's code before approving it.

The agents are given memory together, not each. When they run out, the agent holding most is ended, the chat it was working for says so, and this page shows it as **ended for memory**. How much they are given is set where DecentAI is started: `AGENTS_MEMORY` and `AGENTS_CPUS` in its `deploy.env`.

## Operating

The platform is a backend, an AI runtime, a container the agents run in and a web app, over a database, configured entirely by environment. The repository's documents cover starting it on a computer or a server, what to back up, updating, and every setting.
