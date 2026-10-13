# Providers, safety and monitoring

What the organization is set up with, and what it can see of its agents: the model providers the assistant thinks with, the apps members connect their accounts through, what agents may do without asking, and what they were seen doing. The first three are under **Settings**; Monitoring is its own page, under **Organization**.

## Model providers

**Settings → Model providers** holds the providers the organization has a key for. Adding one is choosing the provider and pasting its key: the address is filled in, and the provider is asked whether the key works before it is saved. One key serves every model the provider has — the models a chat thinks with, and the ones that embed and write speech down. A model on your own computer or network is added as *Your own server*, by its address.

A provider is private to whoever added it until they share it, and the first one added is the default. Share one with everyone for others to use it — and for speech and agent routing, which take only a provider shared with everyone. One provider is the default for every chat that did not choose; a person's last pick is what their next chat starts with; a chat may choose any model of any provider its person can see. Keys are write-only.

**Settings → Speech** holds speech: what writes a spoken message down, and what says a reply aloud. Each is *On this machine* — the platform's own models, running beside it, so that nothing spoken or said leaves the machine — or *A provider*, shared with the whole organization, and one of its models, or *Off*. Speech to text starts on this machine; text to speech starts off.

The platform's own models are downloaded once, when first chosen, and the page says how far a download is. The same page chooses the embedding model agent routing finds the right agent with, a provider shared with the whole organization and one of its models. The same page holds *Your chat defaults* — the model, trust level, turn budget and number of skills a new chat of yours starts with — your notifications, and the numbers agent routing works by.

## Connected apps

**Settings → Connected apps** holds what the organization registered with providers — Google, Microsoft, Slack — so members can connect their accounts with a click. One registration per provider: register an app in the provider's console with the *OAuth redirect URL* the page shows, and paste the client id and, where the provider issued one, the secret here.

The registration also records where the provider is: its consent, token and account addresses. The page fills them in from what the installed agents name; where agents name different addresses you choose which ones the registration uses, and where no installed agent names the provider you type them.

An agent that names other addresses cannot connect through the registration, and a registration saved without addresses has to be opened and saved again before anyone can connect through it. The provider is chosen, not typed: the page offers first the ids installed agents declare — with the agents that need them — then the known ids by convention (`google`, `microsoft`, `slack`, `github`, `gitlab`, `atlassian`, `salesforce`, `hubspot`, `zoom`, `notion`, `dropbox`, `box`), and a free box for any other id an agent's manifest spells.

The secret is write-only. Members never see it; when an agent needs that provider, their Connect button uses it. Removing a registration stops new connections; accounts already connected keep working until their tokens expire, then ask to be reconnected.

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

**Organization → Monitoring** shows what your organization's agents use and what was seen of them. Administrators see it; anyone else needs the `agents:monitor` actions. Nothing on the page changes anything.

The page has three views: **Agents**, **Events** and **Connections**.

- **Agents** opens with **The platform** on an install that is one organization's: what each part holds now — the backend, the runtime, the agents' container and the database, with what the database keeps on disk. Then the agents: how much memory and how many processors they are given together, how much they hold now, and for each running agent its memory, its share of the processor, how many processes it runs and how much it keeps on disk. **Files** lists what an agent keeps in its own folder. An agent is listed only while it runs: it starts when a chat first calls it.
- **Events.** When each agent started and ended and why, each line it wrote to its log, each process it started, and each job the platform did to it. Choose an agent or a kind to narrow the list.
- **Connections.** Every connection an agent asked for, made or refused, with the site, how much was sent and received, and why one was refused.

What a connection carried is never read: the platform sees a site's name and how many bytes passed. A process that lasted under a second can be missed. An agent's own log is the agent's to write, so read an agent's code before approving it.

The agents are given memory together, not each. When they run out, the agent holding most is ended, the chat it was working for says so, and this page shows it as **ended for memory**. How much they are given is set where DecentAI is started: `AGENTS_MEMORY` and `AGENTS_CPUS` in its `deploy.env`.
