# Schedules

Chats set things in motion that outlive the moment they were open. **AI → Schedules** lists what every chat of yours keeps on the clock.

## Schedules

A schedule is something the assistant's clock does later: once, at a time; every so many minutes, hours or days; or on a calendar expression such as *every Monday at 09:00*, always in the chat's time zone.

Two kinds:

- **A reminder** wakes the assistant with a note. It then does whatever the note asks — check, summarise, tell you.
- **A function on a clock** runs an agent's function unattended, with fixed inputs, and wakes the assistant only when a named field of the result comes back non-empty. Only functions an agent marked *schedulable* can be run this way, and they are always reads or ordinary changes — never something that leaves the platform. This is how *tell me on Friday what is still outstanding* costs nothing until something is outstanding.

You can make a schedule from a chat, in words, or from the Schedules page: describe it and the assistant sets it, or fill in the form and it goes straight on to the clock. Each schedule shows its next run, the runs after that, and what its last runs did. Pause, resume or delete it there.

## Cards waiting on you

Every call that costs more than its chat's level waits for a person, on a card in its chat. A chat with a card waiting is marked in the chat list and counted in the sidebar; open the chat to decide. **Settings → Chat configuration** lets you be notified on your devices, and by email when none can be reached.

An approval waits for you; it does not run out by itself. It ends unanswered when its chat is stopped, and the assistant does not proceed. A *question* an agent asked is different: one nobody answers within a day expires.

## Background work

Some work runs in the background while the chat goes on: a long import, a helper assistant working on a bounded goal. It is shown in its own chat, in a bar under the chat's header — *Working in the background* — and as a *Working* flag on the chat in the list; when it finishes the chat is told and picks up from there.

## When everything is stopped

The stop sign at the top of every page stops all of your chats, and holds the clock too. Your schedules stay listed, the Schedules page says that everything is stopped, and none of them fires until you press **Resume**.
