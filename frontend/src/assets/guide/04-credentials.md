# Credentials

A credential is something the platform holds so that an agent can act on an outside service — a Google account, an API token. Its secret values are encrypted, are not shown again on any page, and are never given to the assistant's model. The agent that uses a credential does receive its values, when one of its functions runs.

## Where they live

**Secrets** is the page. Each credential is made against a **definition**: the fields it has, which are shown (a hostname, an account email) and which are encrypted (a token, a client secret). Nobody writes a definition by hand. Installing an agent derives one from each credential shape its manifest declares, so the form you fill in is generated from the reviewed manifest; a login an agent asks for as it works gets one for its site.

The encrypted half is written once and never returned by any read. What a person can see of a credential is its plain half — enough to tell two apart.

## Connected accounts

Some credentials are not typed in. When an agent's credential is a Google, Microsoft or similar account, its row on the Secrets page and the agent's Credentials tab shows **Connect account** instead of a form. You sign in with the provider in a new window, allow what the agent asked for, and come back with the credential made and named after the account. The platform keeps the tokens and renews them; the agent only ever receives a short-lived access token.

If the provider stops accepting the grant — you changed your password, an administrator revoked it — the credential shows **needs reconnect**, the agent says so in the chat, and **Reconnect** signs you in again without making a second credential.

Connecting needs the organization to have registered its app with that provider once, under **Settings → Connected apps**. Until it has, the Connect button says whom to ask.

## Logins agents ask for as they work

Some agents cannot know in advance which sites you will send them to — a browser agent most of all. When such an agent reaches a login form it does not have a login for, it asks you in the chat: a card names the site and the fields the form wants, you type them, and the login is saved as one of your secrets under that site's name. The values are encrypted here and never shown to the assistant; the agent is handed them only to sign in.

The next time, no card. A saved login is used again only by the agent you typed it for, on the site you typed it for: another agent, or the same agent on another site of the same company, shows a consent card first, and you can allow it, decline, or update the details. Two accounts on one site are two logins, and the agent asks which. A code the site sends you each time is asked each time and kept nowhere.

Saved logins sit in their own section on the Secrets page: which agents are allowed on which sites, with each consent revocable, and the same sharing, hand-over and delete as any secret.

## Who may use one

A credential is private to the person who made it until they share it, and sharing is deliberate: with named groups, named people, or — with a specific grant — the whole organization. You cannot share beyond what you can reach yourself, and only the creator may edit or delete.

The creator can also **hand over** a credential to a colleague, who becomes its owner; everyone it was shared with keeps using it, and agents granted it keep working. The same hand-over exists for records, files, skills, model connections and agent sources.

Being able to see a credential is not the same as an agent being able to use it. A credential saved on an agent's Credentials tab is that agent's own, and the agent uses it. Any other credential reaches an agent only by a **grant to a slot**: on the agent's Credentials tab, a person points one of their visible credentials at the agent's declared slot. Only credentials whose definition matches the slot's fields exactly are offered. The grant can be taken back at any time.

## How an agent uses one

When a function that declared the slot runs, the platform finds the credential for that chat: the one the chat chose, else one lent to the agent, else your default, else the only one you can see. The agent's code asks for it by name — *use the connection* — and receives its values, decrypted, for that call: that agent's code, and no other agent's. The model never sees them. Every use is written to the audit trail as a credential read, by name, never by value.

## Personal credentials for shared agents

A person may keep their own credential — their own mailbox, their own calendar — and set it as their default for a slot. Then the shared Gmail or Outlook agent acts as them in their chats, and as a colleague in the colleague's. One installed agent, many accounts, each person's own.

## When an agent is updated

An update may change the fields an agent's credential has. Credentials saved for that agent move to the new shape by themselves wherever the new shape asks for nothing they lack. One that lacks a newly required field is marked **outdated** on the Secrets page: open it, fill in what is asked, and save. A credential lent to the agent from elsewhere that no longer matches is refused when the agent tries to use it, and the agent's Credentials tab says it no longer fits: take it back and give the agent a credential of its own.

## Model provider keys

The keys the assistant thinks with are a credential of their own kind, under **Settings → Model providers**: a provider and the key to it, write-only. One key serves every model that provider has. The organization marks one provider as the default; a chat may choose any model of any provider its person can see. The key reaches the runtime only through the platform, per chat, and never reaches an agent.

## Rotating and removing

Editing a credential with a blank encrypted field keeps the stored value; filling it replaces it. Deleting a credential that an agent is granted is refused until the grant is removed, so an agent is never left pointing at nothing without someone deciding that.
