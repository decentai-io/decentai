# Schedules

Chats set things in motion that outlive the moment they were open. **AI → Schedules** lists what every chat of yours keeps on the clock.

## What a schedule is

A schedule is something the assistant's clock does later: once, at a date and a time; every so many minutes, hours or days; or on days of the week at a time, such as *every weekday at 09:00*.

Two kinds:

- **A note that wakes the assistant.** It reads the note in the chat and does what it says: check, summarise, tell you.
- **A function on a clock** runs an agent's function unattended, with fixed inputs, and wakes the assistant only when a named field of the result comes back non-empty. Only functions an agent marked *schedulable* can be run this way, and they are always reads or ordinary changes, never something that leaves the platform. This is how *tell me on Friday what is still outstanding* costs nothing until something is outstanding.

## Making one

There are three ways. Ask for it in any chat, in words. Describe it in the line at the top of the Schedules page and press **Ask the assistant**: a chat opens and the assistant sets it. Or press **New schedule** and fill it in yourself, and it goes straight on to the clock.

The form asks which chat it lives in, what it does, and when. *When* is one of four: **Every** so many minutes, hours or days; **On days** of the week at a time; **Once**, on a date; or **Custom**, a cron expression with its five parts named under it. As you choose, the form shows the next runs it comes to.

Times are read in the schedule's own **time zone**, which starts as yours and can be any other: a report for an office elsewhere is read in its day.

## Reading the list

Each row says when the schedule comes round, in words, with its zone; what it does and in which chat; when it runs next and how the last run went. A chip says how it stands: **Active**, **Paused**, **Done** for a one-off that has run, or **Failing** when its last run failed. The column at the left narrows the list by those, and by chat.

The switch on a row pauses and resumes it. A resumed schedule counts its next run from then: nothing it missed is run. The row's menu holds **Edit**, **Run now**, **Open the chat** and **Delete**.

## A schedule's page

A row's name opens the schedule beside the list: its next three runs, and every run it has had. A run opens to what it gave back, or to why it failed, and **Failed** shows only those.

**Edit** opens the same form, on what the schedule holds. What you do not change stays as it was; a new *when* counts its first run from now. **Run now** moves the next run to this moment: the clock runs it as it runs any other, and its cadence goes on from there.

A chat that keeps schedules shows a clock with their number in its header, which opens this page narrowed to that chat.

## Cards waiting on you

Every call that costs more than its chat's level waits for a person, on a card in its chat. A chat with a card waiting is marked in the chat list and counted in the sidebar; open the chat to decide. **Settings → Notifications** lets you be notified on your devices, and by email when none can be reached.

An approval waits for you; it does not run out by itself. It ends unanswered when its chat is stopped, and the assistant does not proceed. A *question* an agent asked is different: one nobody answers within a day expires.

## Background work

Some work runs in the background while the chat goes on: a long import, a helper assistant working on a bounded goal. It is shown in its own chat, in a bar under the chat's header — *Working in the background* — and as a *Working* flag on the chat in the list; when it finishes the chat is told and picks up from there.

## When everything is stopped

The stop sign at the top of every page stops all of your chats, and holds the clock too. Your schedules stay listed, a line under the top bar and on the Schedules page says that everything is stopped, and none of them runs until you press **Resume**.
