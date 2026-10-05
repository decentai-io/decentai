# Chats

A chat is a conversation with the assistant and the agents you enabled for it. This chapter is about what happens when you ask for something, and how to read what comes back.

## Your chats

**AI → Chats** opens on a box to type in: write there to start a new chat. Beneath it are all your earlier chats, newest first and grouped by day, in a list that scrolls on its own. Type in *Search chats* to narrow it by title — Enter opens the first match. Point at a chat to archive it, which puts it out of the way, or to delete it, which asks first and cannot be undone. Switch the list to **Archived** to find a chat you put away and restore it. Inside a chat, **Chats** at the top left brings you back.

## Asking

Say what you want in plain words, with the facts the work needs: names, dates, amounts, which notebook, which customer. The assistant reads the manifests of the enabled agents, so it knows what each can do, what inputs each function needs, and what each costs.

Good asks name the outcome, not the steps:

> Find Harbourline's request in my mailbox, compare the three chair quotations we received, and tell me which meets our requirements.

The assistant decides which functions to call and in which order. When it lacks a fact it needs — an exact date, an owner for a task the notes did not name — it asks you rather than guessing.

## What it is doing

While the assistant works, one line under its name says what it is doing — *Searching with Notebook…* — and the answer arrives beneath that line. When it is done the line settles to a summary such as *Worked through 3 steps · 4.1s*. Click the line to see the steps: each function it called, the agent's own last word on it, how long it took, and whether it failed.

For anything that takes more than one step, the assistant writes a short plan, and the line opens to that instead: each item, whether it is to do, in progress, done, or blocked, and what proved it done. A plan is a record the platform checks — an item marked done with nothing behind it is shown as *unverified*. When the answer lands and everything is done the plan folds back into the line; it disappears when your next ask begins. A plan that is waiting on your answer stays until you give it.

## Approvals

When a function costs more than the chat's level, a card appears asking you to approve or deny that exact call — the agent, the function, the inputs. Approve, and the call runs with those inputs and no others. Deny, and the assistant is told, and works around it or stops.

A chat with a card waiting is marked in the chat list and counted in the sidebar, and you can be notified on your devices or by email (**Settings → Chat configuration**), so a decision does not wait for you to look. Approvals are recorded in the audit trail with who decided and when.

## When an agent asks

An agent may need a decision only you can make — *"Ship it" is already in "work". Update that note, or keep both?* A card appears in the chat with the question and the agent's name, a button for each choice it offers, and a box to answer in your own words. The agent waits while you decide, and its time limit does not run meanwhile. A question nobody answers within a day expires, and the agent goes on with its safe default; agents are expected to ask *before* they change anything, so a question left unanswered should leave things as they were.

A scheduled run may ask too: the card waits in its chat until you answer or it expires.

## What comes back

The assistant's words are its own. What appears *beside* them is the platform's:

- **Verified** marks on writes — a note saved, a task created, a record updated — appear only when the call succeeded.
- **Tables and charts** are stored results, rendered from storage: rows a call returned that the assistant chose to show, with the columns it picked, or a view the agent itself built. Beneath each is the agent it came from — *from Outlook*. The assistant shows one when the answer is the rows, not when the rows were the means to a sentence.
- **Files** it produced — a document, a workbook, a statement — appear as file cards you can open or download.
- **Messages from an agent**: an agent may report something itself — an import finished, and what came in — shown under the assistant with the agent's name beneath it. The assistant hears it too, and does not say it again.

If the assistant claims something the platform could not verify, there is no mark beside it. Treat a bare claim as a claim.

## Attachments

Drop a file into the chat and it is stored for that chat and offered to the assistant by reference. Agents that read documents, spreadsheets or receipts take that reference as an input. The original file is never changed by an agent; anything it produces is a new file.

The folder button beside the paperclip attaches a file the platform already holds — one you uploaded on the Files page, attached in another chat, or an agent produced — by reference, without uploading it again. Mention a file without attaching it — *summarize the report I uploaded yesterday* — and the assistant looks through the files you can see and puts its best guesses on a card. Tick the ones you meant, search for any it missed, or choose none. What you pick is attached to the chat as if you had dropped it in. Nothing is used without your say-so.

An agent that drives something visible, a browser above all, can show it in the chat as it goes: a *Live view* appears above the composer with what the agent sees. *Take over* puts your mouse and keyboard on it, for a CAPTCHA or a sign-in a site wants a human for; *Hand back*, or Esc, returns control to the agent.

An agent that reaches a login form it has no login for asks you on a card: the site, the fields, and where the login is used. What you type is saved as one of your secrets and never shown to the assistant; the *Credentials* chapter has the rules.

An agent that wrote code and wants to run it — a script in a page it has open, most often — shows it to you on a card first. The card says what the code is for, what it needs (packages, sites, credentials, files) and what the assistant made of it when it read the code: that it does what it says, or that it does more. The code itself is on the card, to read or to fold away. *Allow* runs it; *Don't allow* stops it, and the agent is told not to try the same another way.

The Code agent works this way for anything that needs a program: it writes one in Python for what you asked, and its card is where you decide. What the card names is what the program gets — those sites are opened for that one run and those packages are installed by the platform — and a program that fails is corrected and shown to you again before it runs again.

How often you are asked is yours to set, on **Settings → Safety**. When your setting lets code run without a card, the agent still says so in the chat, with the code.

Plain documents — text, Markdown, CSV, JSON, a PDF with real text in it — the assistant reads itself, a page at a time, and answers from. Pictures it looks at. Spreadsheets, Word documents and scans go to an agent that opens that kind of file.

## Memory

Say *remember that I prefer concise updates with the risks first* and the assistant saves it, announcing that it did. What it remembers is on **Settings → Memory**, yours to correct or delete. Nothing is remembered silently.

## Schedules from a chat

Ask for something *on Friday morning* or *every Monday at 9* and the assistant sets a schedule: either a reminder that wakes it with a note, or — when an agent offers a function designed for it — that function run unattended, waking the assistant only when there is something to report. Every schedule is announced when set and visible on **AI → Schedules**.

## Time zone and models

Each chat has a time zone the assistant counts in — "Friday 9 am" is your Friday — and a language model, named under the box you write in. Press the name to choose another: every model of every provider you have a key for is listed, the ones you picked lately first, and typing narrows the list. For a model that thinks before answering, how hard it thinks is chosen beside it. The change applies from the chat's next turn, and your next new chat starts with the model you last picked.

## The chat's activity and audit

The *Activity & audit* dialog on a chat is the platform's record of that chat: every function that ran, with the agent, its level, the inputs in outline, whether it succeeded, how long it took, and the result it stored; every approval asked and answered; every credential read by name. It is written by the platform as the work happens and cannot be edited.

Beside it, **The assistant** shows what the model of this chat was shown and what it answered, in order: its instructions, each thing that arrived, and the action it chose each time. Open an entry to read it whole. It is yours to read and nobody else's, an administrator included. The earlier part of a long chat is there as the summary it was folded into.

## Stopping everything

The stop sign at the top of every page ends all of your chats at once: running work, helpers, browsers, questions waiting on you, and scheduled runs. Nothing of yours starts again until you press **Resume** in the bar that appears. It stops your own work only, and what was already done stays done.
