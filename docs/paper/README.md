# DecentAI: A Secure Operating System for AI Agents

*The model proposes; the platform disposes.*

> **Draft.** Every claim the paper makes names the code that keeps it,
> and the documentation tests check that every path it names exists.

## How to read this paper

Every section is written twice over.

- **The plain layer** is the main text. It needs no background beyond
  knowing that a computer runs programs and that the internet is other
  computers. Read only this and you will understand the whole design.
- **For the specialist** blocks fold out beneath it. They hold the
  mechanism exactly — the system calls, the token formats, the order
  things happen in and why — with the file in the code that does it.
  They are written for engineers and researchers in AI systems,
  operating systems and security, and they are where to check us.

One request runs through the whole paper, so that each section is a
closer look at one step of it:

> *"Email the three customers whose invoices are overdue, and remind me
> on Friday if any of them hasn't paid."*

## Contents

| # | Section | In one line |
|---|---|---|
| 0 | [Summary](00-summary.md) | the claims, each with the section that keeps it |
| 1 | [The problem](01-the-problem.md) | an assistant that does things can invent, overstep and leave no trace, and its tools are strangers' code |
| 2 | [The idea](02-the-idea.md) | the model proposes, the platform disposes: an operating system in which the AI holds no authority at all |
| 3 | [One request, end to end](03-one-request.md) | the example, followed from the app to an agent and back |
| 4 | [Who you are, and what you may do](04-who-you-are.md) | nothing allowed by default, checked on every request, and two invariants |
| 5 | [The assistant](05-the-assistant.md) | small steps, a plan that needs proof, and a person's yes for what costs more than the chat allows |
| 6 | [Nothing invented](06-nothing-invented.md) | what you see beside the AI's words was read from a real result |
| 7 | [Agents: a contract before code](07-agents.md) | an agent says what it will do, keep and reach, and is held to it |
| 8 | [The sandbox](08-the-sandbox.md) | a locked room for every agent, and a building inspector who says what is not locked |
| 9 | [Secrets and data](09-secrets-and-data.md) | a password reaches the agent it was lent to, and never the AI |
| 10 | [Stopping and watching](10-stopping-and-watching.md) | a stop that stops, and a record the platform only adds to |
| 11 | [What it does not do](11-what-it-does-not-do.md) | the limits, said plainly |
| 12 | [How we know](12-how-we-know.md) | the claims are tested, including by an agent written to break out |
| — | [Appendix](13-appendix.md) | the words, and where each part lives in the code |
