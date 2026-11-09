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
program served that does not hash to its approved digest, a runtime
acting for a chat it does not hold, one organization seeing another's
records, a node admitted without a certificate that leads to the
deployment's roots, or a runtime given a chat, a credential or a model
key of an organization it may not serve.

Each run of an agent is in a jail of its own, as an unprivileged user,
with only its own files, and reaches only the hosts its manifest
declared, through the runtime's way out ([the sandbox](documentation/sandbox.md)).
A way past any of that is in scope too.

## What is deliberately out of scope

- **An agent misusing what it was rightly given.** An agent receives
  the secrets granted to it, decrypted, and may reach the hosts it
  declared; what it sends there is its own doing. Reading the manifest
  and the code before approving is the control.
- **A sandbox started without what it needs.** A sandbox proves at every
  start that a jail can be made and that its firewall rule holds, and
  does not start otherwise. A stack that takes away what
  `deploy/docker-compose.yml` gives it is outside this.
