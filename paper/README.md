# The paper

*The model proposes; the platform disposes.*

The design of DecentAI as one argument, read for its reasons:
[the white paper](whitepaper/decentai-whitepaper.pdf). [`documentation/`](../documentation/README.md)
says what each part is and does. The paper says why the parts are what they are, and what is
at stake at each boundary.

**Its mechanism chapters (5 to 14) and its test counts describe the design before the
redesign of November 2026.** Its argument holds; where a mechanism differs,
[`documentation/`](../documentation/README.md) is the design.

## How to read it

It is written for two readers.

- **To get the argument,** read each chapter's opening and the short statements
  with a green bar.
- **To check us,** read what sits under a **Mechanism** bar: the exact mechanism,
  and beside it the files that do it.

| Part | Chapters | What it is for |
|---|---|---|
| The argument | 1 to 4 | the problem, the idea, the parts, and one request end to end |
| The mechanisms | 5 to 14 | permissions, the gate, agents and approval, the worker, the sandbox, the way out, secrets, certificates, evidence, stopping |
| Running and doubting it | 15 to 19 | layouts, what leaves a deployment, what a taken-over part gets, what is tested, and the limits |

## Building it

The source and the build are in [`whitepaper/`](whitepaper/README.md).

```powershell
.\whitepaper\build.ps1
```

`documentation/` and the code are the sources of truth. Where the paper states a mechanism,
the change that alters it belongs here too.
