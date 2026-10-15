# Connecting apps

Agents that work in a person's own account — Gmail, Outlook, Slack,
Dropbox and the like — need the organization to register **one app per
provider**, once. After that, each person connects their own account
with a click, on the agent's page or when a chat asks.

Everything starts on **Settings → Connected apps**:

1. **Copy the redirect URL** the page shows. Each provider needs it,
   exactly as shown.
2. **Register an app** in the provider's console (below), with that
   redirect URL.
3. **Connect** the provider on the page: paste the client id and, where
   the provider issued one, the secret. The secret is encrypted and
   never shown again.

The agents that need a provider nobody has connected yet are listed on
the page, each with a *Connect* button.

## Google

In the Google Cloud console:

1. Choose or create a project, and turn on the APIs your agents use
   (*APIs & Services → Library*): Gmail, Calendar, Docs, Drive, Forms,
   Sheets, Tasks.
2. Set up the consent screen (*Google Auth Platform*). Choose
   **Internal** if your company uses Google Workspace: only your people
   can connect, and nothing expires. **External** works for anyone, but
   while it is in Testing only the test users you add can connect, and
   their connection lasts seven days.
3. Add the permissions your agents ask for (*Data access*).
4. Create a client (*Clients*) of the type **Web application**, with
   the redirect URL under *Authorized redirect URIs*.
5. Paste its client id and secret here.

## Microsoft

In the Microsoft Entra admin center, *App registrations*:

1. **New registration**, for **accounts in any organizational
   directory** (multitenant) — the agents sign in at Microsoft's shared
   address, which an app for one organization only is refused at.
2. *Authentication → Add a platform → Web*, with the redirect URL. (On
   your own computer: *Mobile and desktop applications*, and no secret.)
3. *Certificates & secrets → New client secret*; copy its **Value**
   straight away. Note when it expires, and replace it here before then.
4. *API permissions → Microsoft Graph → Delegated*: add what your
   agents ask for (mail, calendar, files, tasks, Teams). If your
   organization does not let people agree for themselves, press *Grant
   admin consent*.
5. Paste the *Application (client) ID* and the secret's value here.

## Slack

At api.slack.com/apps: *Create New App → From scratch*. Add the
redirect URL under *OAuth & Permissions* (Slack needs https), and the
permissions under **User Token Scopes**. Paste the *Client ID* and
*Client Secret* from *Basic Information*. For people in other
workspaces, turn on *Public Distribution*.

## Notion, Dropbox, Box, Todoist

- **Notion** — in the developer portal, a **public** connection with
  the redirect URL; paste its OAuth client id and secret. People choose
  which pages to share when they connect.
- **Dropbox** — in the App Console, *Scoped access* and **Full
  Dropbox**; the redirect URL under *OAuth 2*; tick the permissions on
  *Permissions* and *Submit*. The *App key* is the client id; the
  secret may be left empty.
- **Box** — in the Developer Console, a **Custom App** with **User
  Authentication (OAuth 2.0)**; the redirect URL; the scopes *Read all
  files and folders* and *Write all files and folders*.
- **Todoist** — in *App Management*, a new app with the redirect URL as
  its *OAuth redirect URL*.

## When it does not work

- **Redirect URL mismatch** — the URL registered with the provider is
  not exactly the one this page shows.
- **People must reconnect every week** (Google) — the app is External
  and in Testing. Make it Internal, or publish it.
- **An administrator must approve** (Microsoft) — grant admin consent
  once, under *API permissions*.
- **It worked, then stopped** (Microsoft) — the client secret expired.
  Make a new one and use *Replace the secret* here.

The full steps, with every permission each agent asks for, are in the
documentation: *Connecting apps*.
