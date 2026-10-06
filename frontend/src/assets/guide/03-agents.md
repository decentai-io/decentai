# Agents and the marketplace

An agent is a reviewed program the assistant can drive: a manifest that declares exactly what it can do and what it needs, and code that does only that. Nothing ships installed. An organization installs what it needs from a catalog it trusts.

## Where agents come from

A **source** is a git repository that carries a catalog: a file listing the agents it offers, each in its own folder with its manifest. A repository that holds one agent, with its manifest at the top, is a source too. The platform reads the catalog without running any code, validates every manifest, and shows what is on offer.

Administrators add sources on **Agents → Marketplace**, under *Agent sources*, with **Add source**. A source may be private; a credential with read access is stored with it, and the platform fetches with that credential and nobody else's.

A source belongs to whoever added it. They may edit, share, refresh and remove it, and so may anyone holding the *manage any source* grant, which every administrator has. The hand-over button on a source passes it to a colleague; it goes to a successor automatically when its owner is removed from the organization.

## Installing

Every agent carries a few prompts to try first, written by its author. They appear on the agent's page under *Try asking*, each one a click from a chat that sends it. They are the quickest way to see what an agent is for.

An agent that keeps records may also ship **sample data**: the records and files that let you try it before you have any of your own. The marketplace says what a sheet would load; the agent's page loads it with one click as *your own* records and files — private to you, editable, visible under Saved data and Files — and removes it again in one click. Nothing marks them as samples once loaded; the platform simply remembers what it minted for you.


Installing an agent is an **approval** of one exact commit. **Review and install** on an agent's card opens what it declares — its functions and their levels, the hosts it connects to, and how many credentials and packages it needs and whether it keeps data of its own (each is listed by name on the agent's page once it is installed) — and **Approve and install** is at the foot of that page, so one agent is not installed without it having been opened. An update is the same, under **Review update**; an agent already installed shows the page under **Details**. Several at once is the exception: **Install selected** approves every ticked agent without opening any. The platform packages that folder, stores the bytes itself, and installs the declared dependencies into a private environment. The code cannot change under you: an update is a new approval.

**Where it connects** is part of that reading, and stays on the agent's page afterwards. An agent names its hosts; one whose host is yours to say — your own site, your own server — names the field of its credential that holds the address; one whose work is the open web says *any website*. A host is shown with a port beside it (`imap.gmail.com:993`) when the agent speaks something other than the web's protocol there, mail for instance, and it is reached on that port and no other. Every agent has to say: one whose manifest does not is refused, and cannot be installed.

When the source moves ahead, the agents that changed say so, with the version installed and the one waiting — *v1.0.0 installed · v1.1.0 offered* on the marketplace, *v1.1.0 available* on the Installed page. Updating is the same read-and-approve: **Review update** — on the agent's card in the marketplace, on the Installed page and on the agent's own page — opens what the new version declares and what it changes, and **Approve update** is at the foot of that page. When the source moves ahead and its catalog no longer lists an agent you installed — it was renamed or withdrawn upstream — the agent shows *no longer in the source's catalog*: there is nothing to update to, so uninstall it and install its replacement. Uninstall is offered on an installed agent whatever state it is in.

## Granting

Installing makes an agent available; a **grant** makes it usable by a person or a group. The first install grants it to whoever installed it and to every group they are in. Anyone else is given it on **Agents → Installed**, on the agent's **Access** tab, where a grant is also taken away. A grant may narrow what the agent may do — for example, only certain notebooks — using the scopes the manifest declares.

A chat can only enable agents its person has been granted. The assistant cannot reach beyond that. Which of your agents a new chat starts with is yours to say: **In new chats** on each agent on the Installed page, and in a chat's Agents dialog, **Reset to my defaults**.

## Credentials

An agent that talks to an outside service declares the *shape* of the credential it needs: the fields of a login it will be given, or an account you connect by signing in with the provider. It never names a credential. A credential saved on the agent's **Credentials** tab is that agent's, and its functions use it from then on. A credential you already have elsewhere can be lent to the agent instead, when its shape is the same. Either way the values reach that agent's code when one of its functions runs, and no other agent's; the assistant's model never sees them. The next chapter covers this.

## What an agent's page shows

Each installed agent has a page with four tabs: **Overview**, what was approved — where it connects, what it keeps, the packages it asked for, and how it is confined on this install — with prompts to try and its sample data; **Credentials**, shown when the agent declares any, with the credentials saved for it or lent to it; **Functions**, the list of what it can do with the level of each and the records it touches; **Access**, who is granted and with what scope.

## Uninstalling

Removing an agent removes its approval and its grants. Data it kept stays: it remains readable and deletable under **Data → Saved data**, but can no longer be changed through that agent.

A source with agents still installed from it cannot be removed by itself. The dialog lists those agents, and its button uninstalls them and removes the source in one step.

## The reference catalog

The project's own agents are in the [`decentai-agents`](https://github.com/decentai-io/decentai-agents) repository: general-purpose agents for the accounts you already work in (Gmail, Outlook, Google and Microsoft calendars, drives, spreadsheets and documents, Teams, Slack, Notion, any mail account), the files you hand over (Documents, Sheets, Slides, JSON), the public web (a reader, a page watch, feeds), the work nothing else covers (the Browser, and Code, which runs a program you allowed), and what you keep (Notebook, Tasks, Timesheets, Expenses). Each has a README describing what it does, its limits, and what it needs — open one from its page under **Agents**.
