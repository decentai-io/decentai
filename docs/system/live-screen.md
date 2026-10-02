# The live screen

An agent that drives something visible — the Browser agent above all —
shows it in the chat as it goes, and the person can take it over when a
site wants a human: a CAPTCHA, a sign-in a script may not do, a moment
only they should decide. It is a platform capability, not a feature of
one agent: any agent that drives a screen uses the same road. How the
Browser agent itself decides, remembers and reads a page is in its own
README in the agents repository.

## The road a frame travels

The browser runs inside the agent's worker on the runtime host.

    browser ─JPEG─▶ agent ─screen.frame─▶ host ─screen_frame─▶ backend ─▶ page
    browser ◀────── agent ◀─screen.input── host ◀─screen_input── backend ◀─ page

- The agent pushes each frame to the host over the worker pipe as a
  notification (`screen.frame`, [worker protocol](../reference/worker-protocol.md)).
- The host relays it to the chat's audience as a live event
  (`screen_frame`) that is never recorded; the backend forwards it as it
  forwards every runtime frame, and the page draws it.
- Input goes back the same road: the page sends `screen_input` — mouse,
  keyboard, wheel, taking and releasing control, a tab — over the chat
  socket; the backend checks the person may send messages in this chat
  and forwards it; the host hands it to the worker running that call
  (`screen.input`), and the agent feeds it to the browser.

Nothing about the browser is exposed to the network. The person only
ever talks to the platform's authenticated socket, and only the chat's
own audience sees or drives the screen.

**Sizes and rates.** A frame is at most 300 KB (`SCREEN_FRAME_MAX_BYTES`
in `contracts/chat.py`). The Browser agent sends at most four frames a
second at 1280 pixels wide, and only when the picture changed; while
the person drives, frames follow their input but no more often than one
every 120 ms. The host drops a frame it cannot relay rather than
queueing it.

## What an agent uses

No manifest declaration is needed: a screen shows nothing the function
could not already put in a file, and input reaches only the function
that showed it.

| `call.screen…` | Does |
|---|---|
| `show(jpeg, width, height, tabs=None)` | pushes a frame; `tabs` tells what else is open — a name, an address, which one is in front — and the live view shows them above the picture |
| `close()` | ends the stream |
| `inputs()` | drains the events the person sent since last asked; a hand on a tab arrives as `{"type": "tab", "action": "switch" \| "close" \| "new", "index"}` |
| `taken` | whether the person holds control now |
| `said()` | drains what the person wrote in the chat while this call runs, so an agent hears "only the 20 kg one" at its next step |

**`call.conversation`** is the chat's opaque key. A worker serves every
chat of an organization, so what a function keeps between calls — the
Browser agent's open browser, for instance — is kept under this key and
handed to no other conversation. Outside a chat it is empty and
nothing is kept.

**A function marked `watch: true`** (level 0, no model) is what the
platform calls when the person asks to see the browser before anything
is asked of it: the chat header's browser button sends `AI:Chat:Watch`,
the session calls that function directly, and it streams until the
panel is closed or a run takes the browser. Without such an agent in
the chat the page is told `screen_unavailable`.

## What the person sees

- **A live view**, shown whenever a frame arrives: beside the thread on
  a wide screen, above the composer on a narrow one. It carries the
  agent's name, the agent's latest step in its own words, and how old a
  still frame is once it passes a few seconds, so a thinking agent and
  a stuck one look different.
- **Take over and Hand back.** Taking the browser pauses the run: frames
  still flow, the person's clicks, keys and wheel go to the page, scaled
  to the frame's size, and Hand back resumes with the agent told to look
  again. Enter on the picture takes over; Esc hands back. A hand on a
  tab takes control first.
- **The last frame stays** after a call ends, marked idle, until the
  next run replaces it. The panel can be minimised, closed, and opened
  again from the header.
- **The composer stays open** during a turn; Stop is its own button.

## Sign-in cards

A site's sign-in is the person's. An agent asks for exactly the fields
a page shows (`call.credential`): what the platform holds for that host
it hands over, what it lacks the person types on a card, and a field
marked `once` — a one-time code — is asked every time and never saved.
Saved values are the person's secrets, kept per host, and reach only
the agent that asked, for that call; the model never sees them. The
agent template's SDK reference has the whole of it.

## What it does not do

- **One socket per chat and person.** The same person watching one chat
  from a phone and a computer at once sees it on one of them.
- **It is a picture, not a remote desktop.** What the person does is
  sent as input events; nothing of the browser is reachable from their
  machine.
