# Getting started

DecentAI is a workspace where an assistant does real work for you through **agents** — small, reviewed programs that can read a mailbox, keep records, fill a document, compare quotations — and where everything an agent does is gated, recorded and readable.

This guide walks the workspace in the order you will meet it. Read this chapter and the next, then come back to the others as you need them.

## Signing in

There is no self-service signup. Your organization's administrator invites you by email; the invitation link lets you set a password. Where DecentAI sends no email, they add you and hand you a temporary password, which you replace with your own the first time you sign in. From then on you sign in with that email and password.

If you forget the password, use *Forgot your password?* on the sign-in page. A reset link is sent to the same address. Where DecentAI sends no email, whoever runs it sets a new password for you on the machine it runs on.

*Keep me signed in on this device* keeps the sign-in for ninety days, past the browser being closed, and offers your email again next time. Leave it off on a computer that is not yours.

## The workspace

The sidebar on the left is the map:

| Section | What is there |
|---|---|
| **AI → Chats** | Where you ask for things. Each chat keeps its own history, its enabled agents and its settings. |
| **AI → Schedules** | What your chats keep on the clock: reminders and functions that run later, and what each run did. |
| **Agents** | The agents installed in your organization, what each may do, and the marketplace they come from. |
| **Data** | Saved data agents keep for you, files you and they have stored, skills, MCP servers, and secrets. |
| **Settings** | Model providers, chat defaults and notifications, memory, API keys, connected apps, safety, monitoring, and the audit trail. |
| **Help → Guide** | This guide. |

People, groups, roles and policies are not in the sidebar. Whoever administers the organization opens the menu on their name at the top right of the page and chooses **Admin console**: the sidebar becomes the administration's — **Account** with your Profile, and **IAM** with Organization, Users, Groups, Roles and Policies — and **Main workspace** in the same menu brings the workspace back.

What you see depends on what your role allows. A page you cannot use is simply not shown; nothing you can see is a page of buttons that refuse.

## Your first chat

1. Open **AI → Chats** and type into the box at the top to start a new chat. Your earlier chats are listed underneath it.
2. Open **More actions** (⋮) in the chat header and choose **Agents** to pick which installed agents it may use. A chat with no agents can talk, but it cannot act in another system.
3. Ask for something in plain words. For example, with the Notebook agent enabled:

> Save a note in my "work" notebook titled "Ideas for Q4" with three bullet points about the Harbourline fit-out.

The assistant will open the agent it needs, do the work, and answer. Beneath its words you may see a table of rows it chose to show you — captioned with the agent it came from — or a file it produced. Those come from the platform, not from the model: they are attached only when the work actually happened.

## Three things worth knowing early

- **Trust level.** Every agent function says whether it only views information, makes an ordinary change, makes a change with wider reach, or acts on an outside service. The shield in the chat header shows the chat's **trust level** — *Ask for every change*, *Standard*, *Trusted* or *Autonomous* — and what runs without asking at it. If an action needs more than the chat's level, the assistant pauses and shows you exactly what it wants to do.
- **Nothing is invented.** The assistant's tables and files are rendered from stored results of real calls. Its plan is checked against what actually ran. If it could not verify something, it says so.
- **Everything is recorded.** **Settings → Audit** and each chat's activity view show what ran, the inputs in outline, and the result — written by the platform, never by the assistant.

Next: [Chats](/guide/chats), where most of the work happens.
