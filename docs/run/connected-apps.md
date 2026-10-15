# Connecting apps: Google, Microsoft, Slack and others

An agent that works in a person's own account — their mail, their
calendar, their files — asks for a **connected account**: the person
signs in with the provider, and the platform keeps the tokens
([the data layer](../system/data-layer.md#connected-accounts)). Before
anyone can, the organization registers **one app per provider**, once,
under **Settings → Connected apps**. This page is how, provider by
provider.

## What every provider needs

1. **The redirect URL.** Where the provider sends a person back after
   they say yes. **Settings → Connected apps** shows it, with a copy
   button: `PUBLIC_APP_URL` followed by `/oauth/callback`, or
   `OAUTH_REDIRECT_URL` where that is set
   ([configuration](configuration.md)). Register it with the provider
   exactly as shown — the scheme, the host, the path, no trailing slash.
2. **A client id and a client secret.** What the provider gives the app
   you registered. Paste both on the page; the secret is encrypted when
   saved and never shown again. Some providers issue apps with no
   secret (noted below).
3. **The permissions the agents ask for.** Each agent's manifest names
   the scopes its credential asks for, and a person is asked to agree
   to them when they connect. Where a provider makes you list them on
   the app as well, the lists below are what the published agents
   ask for; leave out the ones of agents you will not install.

Providers want the redirect URL on **https**, and most make an
exception for `localhost`: a deployment on its own computer, at
`http://localhost:4280`, is accepted by Google, Microsoft (as a desktop
app), Dropbox and Box. **Slack takes https only**; for it — and for any
provider that refuses the local address — a tunnel that gives the
computer an https address, set as `PUBLIC_APP_URL`, is the way.

| Provider | The agents that use it | Where the app is registered |
|---|---|---|
| [Google](#google) | Gmail, Google Calendar, Docs, Drive, Forms, Sheets, Tasks | Google Cloud console |
| [Microsoft](#microsoft) | Outlook, Microsoft Calendar, OneDrive, Excel Online, Word Online, Microsoft Forms, Microsoft To Do, Teams | Microsoft Entra admin center |
| [Slack](#slack) | Slack | Slack's app pages |
| [Notion](#notion) | Notion | Notion's developer portal |
| [Dropbox](#dropbox) | Dropbox | Dropbox App Console |
| [Box](#box) | Box | Box Developer Console |
| [Todoist](#todoist) | Todoist | Todoist App Management |

## Google

In the [Google Cloud console](https://console.cloud.google.com/):

1. **Choose or create a project** for DecentAI.
2. **Turn on the APIs** the agents call, under *APIs & Services →
   Library*: Gmail API, Google Calendar API, Google Docs API, Google
   Drive API, Google Forms API, Google Sheets API, Google Tasks API —
   the ones whose agents you will install.
3. **Set up the consent screen**, under *Google Auth Platform*
   (formerly *OAuth consent screen*): the app's name and a support
   email, then the **audience**:
   - **Internal** — only people in your Google Workspace organization
     can connect. No review by Google, and nothing expires. The right
     choice for a company on Google Workspace.
   - **External** — anyone with a Google account. While the app is in
     *Testing*, only the test users you add (at most 100) can connect,
     and **their connection expires after seven days** — they connect
     again. Publishing it for everyone, with the Gmail and Drive
     permissions the agents ask for, needs Google's verification and an
     independent security assessment.
4. **Add the permissions** under *Data access*: `openid`,
   `…/auth/userinfo.email`, and for each agent you install —
   Gmail `…/auth/gmail.modify`, Calendar `…/auth/calendar`, Docs
   `…/auth/documents`, Drive `…/auth/drive`, Sheets
   `…/auth/spreadsheets`, Tasks `…/auth/tasks`, Forms
   `…/auth/forms.body.readonly` and `…/auth/forms.responses.readonly`.
5. **Create the client**, under *Clients → Create client*: the type
   **Web application**, and under *Authorized redirect URIs* the URL
   from **Settings → Connected apps**.
6. **Paste** the client id and the client secret into **Settings →
   Connected apps → Google**.

## Microsoft

In the [Microsoft Entra admin center](https://entra.microsoft.com/),
under *Identity → Applications → App registrations*:

1. **New registration.** Give it a name, and under *Supported account
   types* choose **Accounts in any organizational directory**
   (multitenant) — and personal Microsoft accounts too, if people will
   connect Outlook.com or a personal OneDrive. The agents sign in at
   Microsoft's shared address (`login.microsoftonline.com/common`),
   which an app registered for one organization only is refused at;
   who may use the app is still decided by each organization's own
   Entra settings.
2. **Add the redirect URL**, under *Authentication → Add a platform*:
   **Web**, with the URL from **Settings → Connected apps**. For
   DecentAI on a computer of your own, choose **Mobile and desktop
   applications** instead: it needs no secret.
3. **Make a secret** (Web only), under *Certificates & secrets → New
   client secret*. Copy its **Value** — not its *Secret ID* — at once;
   it is not shown again. A secret expires (24 months at most): put a
   new one into DecentAI before it does, with *Replace the secret* on
   **Settings → Connected apps**.
4. **Add the permissions**, under *API permissions → Add a permission
   → Microsoft Graph → Delegated permissions*: `openid`, `email`,
   `offline_access`, `User.Read`, and for each agent you install —
   Outlook `Mail.ReadWrite` and `Mail.Send`, Calendar
   `Calendars.ReadWrite`, OneDrive, Excel and Word
   `Files.ReadWrite.All`, To Do `Tasks.ReadWrite`, Teams
   `Team.ReadBasic.All`, `Channel.ReadBasic.All`, `ChannelMessage.Send`
   and `Chat.ReadWrite`. None of these needs an administrator's
   consent by Microsoft's own rules; where your organization has turned
   off people agreeing for themselves, an administrator presses *Grant
   admin consent* here once.
5. **Paste** the *Application (client) ID* and the secret's value into
   **Settings → Connected apps → Microsoft** (no secret for a desktop
   app).

## Slack

At [api.slack.com/apps](https://api.slack.com/apps):

1. **Create New App → From scratch**, with a name and the workspace it
   belongs to.
2. **Add the redirect URL**, under *OAuth & Permissions → Redirect
   URLs*. Slack takes only https.
3. **Add the permissions as User Token Scopes** — not Bot Token
   Scopes: the agent acts as the person who connected, in the channels
   they are in. `channels:history`, `channels:read`, `chat:write`,
   `groups:history`, `groups:read`, `im:history`, `im:read`,
   `mpim:history`, `mpim:read`, `search:read`, `users:read`,
   `users:read.email`.
4. **Paste** the *Client ID* and *Client Secret*, from *Basic
   Information → App Credentials*, into **Settings → Connected apps →
   Slack**.

An app is installable in the workspace that made it. For people in
other workspaces, turn on *Manage Distribution → Public Distribution*.

## Notion

In Notion's [developer portal](https://app.notion.com/developers/connections):

1. **New connection**, of the **public** kind — the kind people sign in
   to. Choose which workspaces may install it.
2. **Add the redirect URL** in its OAuth settings.
3. **Paste** the *OAuth client ID* and *OAuth client secret*, from its
   *Configuration* tab, into **Settings → Connected apps → Notion**.

When a person connects, Notion asks them which pages to share with it:
the agent reaches those and no others.

## Dropbox

In the [Dropbox App Console](https://www.dropbox.com/developers/apps):

1. **Create app**: *Scoped access*, and **Full Dropbox** — the agent
   works in the person's own folders, not one of its own.
2. **Add the redirect URL**, under *Settings → OAuth 2 → Redirect URIs*.
3. **Tick the permissions**, under *Permissions*: `account_info.read`,
   `files.metadata.read`, `files.content.read`, `files.content.write`,
   `sharing.read`, `sharing.write` — then *Submit*.
4. **Paste** the *App key* as the client id into **Settings →
   Connected apps → Dropbox**. The *App secret* may be left empty.

A new Dropbox app is in development, which limits how many people can
connect; *Apply for production* in the App Console when more need to.

## Box

In the [Box Developer Console](https://app.box.com/developers/console):

1. **New App → Custom App**, with **User Authentication (OAuth 2.0)**.
   The kind cannot be changed afterwards.
2. **Add the redirect URL**, under *Configuration → OAuth 2.0 Redirect
   URI*.
3. **Tick the scopes**, under *Application Scopes*: **Read all files
   and folders stored in Box** and **Write all files and folders stored
   in Box**.
4. **Paste** the *Client ID* and *Client Secret*, from *OAuth 2.0
   Credentials*, into **Settings → Connected apps → Box**.

Where a company's Box administrator approves apps before they are used,
they enable it in the Box Admin Console.

## Todoist

In Todoist's [App Management](https://app.todoist.com/app/settings/integrations/app-management):

1. **Create a new app**, with a name.
2. **Add the redirect URL** as its *OAuth redirect URL*.
3. **Paste** the *Client ID* and *Client secret* into **Settings →
   Connected apps → Todoist**.

## When a connection does not work

- **"redirect_uri mismatch"**, in any provider's words: the URL
  registered there is not the one **Settings → Connected apps** shows —
  a trailing slash, http instead of https, another host.
- **A person connected last week and must again** (Google): the app is
  External and in Testing. Make it Internal, or publish it.
- **Microsoft says the app is not multitenant**: it was registered for
  one organization; change *Supported account types* (step 1).
- **Microsoft says an administrator must approve**: the organization
  does not let people consent for themselves; an administrator grants
  consent once under *API permissions*.
- **Slack says the app is not distributed**: the person is in another
  workspace; turn on public distribution.
- **A connection stopped working after a long while** (Microsoft): the
  client secret expired; make a new one and replace it on the page.
