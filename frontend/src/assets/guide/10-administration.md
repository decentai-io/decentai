# Administration

Who exists, and what each of them may do. It is reached from the menu on your name at the top right of the page, where **Profile** opens your own page and **Admin console** shows **Account** with your **Profile**, and **IAM** — the **Organization** (its name), Users, Groups, Roles and Policies — to those who hold the actions to manage them, and **Main workspace** in the same menu goes back.

## Organizations

A deployment holds one organization. Every user, group, role, policy, credential, record and file belongs to it, and every query is scoped by it.

A fresh organization is seeded with three chains. **Administrators**: a role holding a policy that allows every action.

**Everyone**, the group every user is in implicitly, whose role holds the **baseline** — what any member may do without anyone deciding: their own profile, what the assistant remembers about them, their own API keys, schedules, MCP servers and notifications, their own audit trail, stopping and resuming their own work, reading the organization's skills and its list of model providers, loading an agent's sample data. And **Members**, which grants everyday use. Chatting is Members', not the baseline's.

## People

Invite by email on **Admin console → IAM → Users**; the invitation link lets the person set a password. Where no mail server is set no email is sent, and the page offers **Add a person** instead: it shows you a temporary password to hand over, and the person chooses their own at their first sign-in. **Reset password** on a person's row does the same for one who forgot theirs, whether or not email is sent.

A new person is offered the **Members** group already ticked. It grants everyday use — chats, files, saved data, their own credentials — and nothing of administration. A person in no group at all can sign in and do almost nothing.

Which agents Members may call is decided on each agent: installing one grants it to whoever installed it and to the groups they are in, and its **Access** is where others are given it. Two invariants are enforced: an organization cannot be left with nobody able to manage access, and nobody can grant an action they do not hold themselves.

**Disabling** a person ends their sessions, revokes their API keys, closes their live chats and pauses every schedule they own, keeping all their data. Re-enabling brings the person back; their schedules stay paused until they resume the ones they still want.

**Removing** a person is a hand-over. The dialog shows what they own: their chats, schedules, memories and MCP servers, which are deleted, and their credentials, records, files, skills, model connections and agent sources, which pass to a successor you choose — yourself by default. Whatever they had shared stays shared; agents granted their credentials keep working.

## Operating

The platform is a backend, an AI runtime, a container the agents run in and a web app, over a database, configured entirely by environment. The repository's documents cover starting it on a computer or a server, what to back up, updating, and every setting.

Next: [Roles and policies](/guide/access), how what a person may do is decided.
