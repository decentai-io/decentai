# Security

DecentAI runs code the platform did not write, on a person's or an
organization's own data, under gates they are asked to trust. A weakness in one
of those gates matters more than any other bug.

## Reporting a vulnerability

Please do not open a public issue for a security problem. Report it
privately from the repository's **Security** tab (*Report a
vulnerability*), or email ahmed@decentai.io, with a description, the
steps to reproduce, and the version or commit. You will get an
acknowledgement within three days and a fix or a plan before any public
disclosure.

## What counts

Anything that lets an agent, a model, or a person reach past what the
platform says they may: a function called without its grant, a secret
read by a function that did not declare it, an approval bypassed, a
package served that does not hash to its approved digest, a delegation
used outside its chat, one organization seeing another's records.

Where the platform confines agents — an install made by the launcher,
or the Compose stack in this repository — each agent runs as a user of its own, reads and writes only its own
files, and reaches only the hosts its manifest declared, through the
platform's proxy ([The sandbox](docs/system/sandbox.md)). A way past any
of that is in scope too.

## What is deliberately out of scope

- **An agent misusing what it was rightly given.** An agent receives
  the secrets granted to it, decrypted, and may reach the hosts it
  declared; what it sends there is its own doing. Reading the manifest
  and the code before approving is the control.
- **A stack started without confinement.** Where a part of the sandbox
  does not hold — a stack started by hand without the launcher's
  options, or outside a container — the runtime says so at start and
  an agent's page says what is not enforced there.
