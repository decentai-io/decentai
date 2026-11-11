# Communication

## 1. What it is

The platform's parts are **nodes**: the API server, the runtime, the sandbox and the issuer. Each proves who it is
with a certificate, and they call each other over HTTPS with mutual TLS 1.3 on port 8004. The `communication/` module
is how: a node, its identity, enrolling, and the endpoints it serves. The `issuer/` service certifies them.

| Calls | Whom | On |
|---|---|---|
| API server | runtimes; the issuer | 8004 |
| runtime | API servers, its sandboxes, the issuer | 8004 |
| sandbox | its callers (runtimes), the issuer | 8004; its caller's way out on 8005 |
| any node, at its first start | the issuer | 8004 |

Nothing between two nodes may end their TLS: a proxy in front of port 8004 passes TCP through.

## 2. A node

- **Its port** serves `GET /health`, answered `{"status": "ok"}` to anyone, and `POST /app`, which takes
  `{"endpoint": "Domain:Controller:action", "data": {…}}` as msgpack or JSON and answers in the same form. An
  endpoint is matched ignoring case; an action starting with `_` is no endpoint.
- **A body** is at most 1 MiB unless the node says otherwise; a call takes at most 30 seconds unless the caller says
  otherwise.
- **A refusal** is `{"title", "type": "error", "messages": [{"description"}]}`, with 400, 403, 404, 413 or 500.
- **Who may call** is the node's own rule, asked first with the caller's certificate and the endpoint; `peer` is
  nothing when no certificate was shown. A node that serves only certified callers refuses one with none (401) before
  reading its body.
- **Calling** names the certificate expected at the other end, by id or by kind, and TLS checks it. A node may also
  open a raw TLS stream to another, as the sandbox does to its caller's way out.
- **TLS:** the server asks for a client certificate and takes a call without one only where the node's rule allows;
  it gives no session tickets. The identity folder is read again every minute, so a renewed certificate is used with
  no restart; the roots are read once, at start.

## 3. Certificates

- **Keys** are Ed25519, made where they are kept and never shared.
- **A certificate** names its node's **id**, as its common name and the name `<id>.id.decentai`, and its **kind**, as
  `<kind>.kind.decentai`. Metadata, such as a scope, is JSON in the extension
  `2.25.105172439890329542992363112396306417301`. A peer is known as `{id, kind, key, issued, expires, metadata}`.
- **Checked:** a path to a root; every certificate on it in its time, with a minute's leeway; each signer's path
  length and name constraints; an id and a kind that are single labels.
- **The identity folder** of a node holds `node.pem` (its key, certificate and chain), `roots.pem`, and to enroll,
  `enroll` (one line: `host:port kind`, where its issuer is) and `token`. Every file is mode 600 and written in one
  step. A node with no `enroll` uses the certificate it was given and never renews it.

## 4. Trust

```
decentai-acme-root-1            the root: offline, out of the deployment
  │  signs, by hand, every 120 days
  └── decentai-acme-issuer-1-ca   an issuer's intermediate
        │  signs, by itself
        ├── issuer-9c2f6b         the issuer itself
        ├── api-7f3k2m            an API server
        ├── runtime-4q8w1z        a runtime
        └── sandbox-5k2q7x        a sandbox
```

| Level | How many | Kept where | Lasts | Signs |
|---|---|---|---|---|
| root | 2 per deployment, a primary and a backup | off the deployment, encrypted with a passphrase of 12 or more characters | 10 years, path length 1 | intermediates, by hand |
| intermediate | 1 per issuer | its issuer alone, its key encrypted with `ISSUER_KEY_PASSPHRASE` | 120 days, path length 0, named to `id.decentai` and the kinds it may sign | nodes |
| node | 1 per node | its identity folder | what its token says: 12 hours unless set, at most 1 hour for a launched node, 7 days on one machine | nothing |

- A deployment has roots of its own: a certificate of one means nothing in another.
- Every node trusts both roots from the start, so the backup can take over with nothing sent to the nodes.
- There is no revocation list: certificates are short, and the issuer refuses to renew a node refused.
- **The root tool**, `tools/decentai_root.py`, on the machine that keeps the roots: `init <name>` makes both roots;
  `sign <request> --name <name> --kinds <kinds> [--days N]` signs an issuer's intermediate; `show` reads a
  certificate. It takes `--passphrase-file`, `--root`, `--roots`, `--chain` and `--yes`. On one computer,
  `deploy/setup/setup.py` keeps the roots in `deploy/config/root/` and signs a 3650-day intermediate.

| Where | Holds | Secret |
|---|---|---|
| the offline machine | the primary root's key and certificate | the key, encrypted |
| somewhere else, apart | the backup root's key and certificate | the key, encrypted |
| each issuer | its intermediate's key and certificate; its own identity | both keys |
| every other node | its identity folder | its key, and its token |

| When | What happens | What to do |
|---|---|---|
| an issuer is down | the other issuers go on certifying | nothing |
| every issuer is down | nothing is certified; every node keeps its certificate until it ends, so a node's lifetime is how long the deployment runs without one | bring an issuer back |
| an issuer is taken over | its intermediate certifies nodes until it ends, up to 120 days | don't renew it; make the issuer again, with another key |
| any other node is taken over | it holds no key that signs | replace it, and refuse it at the issuer; its certificate ends within its lifetime |

## 5. Enrolling and renewing

- **The first certificate:** a node with no certificate calls `Certificates:Enrollment:enroll` with its token and a
  certificate request, with no certificate of its own. It checks the issuer's certificate and kind before it sends
  the token. Its id is its kind and 6 random characters; its kind, metadata and lifetime are the token's. One use of
  the token is spent, and given back if signing fails. A node that can't enroll tries again every minute.
- **Renewing** starts at half its lifetime: `Certificates:Certificate:renew` with its certificate, its chain, a new
  request, and that request signed with its current key. It keeps its id, kind and metadata. Renewing is refused
  when its token was withdrawn, its id refused, or the node that launched it is gone; a token expired or used up
  still renews.
- **A certificate that ended** is enrolled again with the token, under a new id. A node that knows itself by its id
  starts again under the new one: the sandbox and the runtime exit, and are restarted.

## 6. The issuer

A process of its own, `python -m issuer`, so that the API server, which people reach, holds no key that signs nodes.
It keeps token hashes and the nodes it certified in a database of its own, `decentai_issuer`.

| Variable | Is | Default |
|---|---|---|
| `ISSUER_IDENTITY` | its identity folder | `/run/decentai` |
| `ISSUER_AUTHORITY` | where its intermediate is kept | `/run/decentai/authority` |
| `MONGO_URI`, `ISSUER_DATABASE` | its database; a password still `change-me` is refused | none, `decentai_issuer` |
| `ISSUER_KEY_PASSPHRASE` | what encrypts its intermediate's key on disk; it doesn't start without one. A key written without one is encrypted when first read | none |
| `ISSUER_PORT` | its port | `8004` |
| `ISSUER_LIFETIME` | a certificate's seconds, when a token says none | `43200` |
| `ISSUER_LAUNCH_RULES` | which kinds may launch which ([7](#7-launching)) | the runtime launches sandboxes |
| `LOG_LEVEL` | its log | `INFO` |

- **Its first start:** it makes `authority.key` and `authority.csr`, logs that it waits for the root with the
  request's fingerprint, and looks for `authority.pem` every minute. Once there, it certifies itself, and serves.
  Before that it answers 503.
- **Its next intermediate:** 30 days before the current one ends it makes `authority.next.*`, to be signed the same
  way. The API server's admin tool asks for the request and installs the result
  (`python -m api_server.admin intermediate request | install --file`).
- **Tokens:** made for a kind, a lifetime (43200 seconds unless said), a number of uses (one unless said) and an
  expiry (7 days unless said), with metadata such as a scope. Never for kind `issuer`. Only a hash is kept. A first
  token for a node Compose doesn't start: `python /opt/decentai/setup/identities.py token <kind>` in the issuer's
  container.
- **What it answers:** enrolling and renewing to anyone; making, withdrawing and listing tokens, listing, refusing
  and allowing nodes, and asking for and installing its intermediate, to nodes of kind `api`; a kind with a launch
  rule makes tokens for, and refuses, the nodes it launched. A node refused makes no tokens, and what its tokens
  would enroll is refused.

## 7. Launching

A node may start another and give it a token of its own making, as a runtime could start sandboxes. Its rule says
what it may make: `{"runtime": {"kinds": ["sandbox"], "copy": ["scope"], "uses": 1, "expires": 300, "lifetime":
3600}}` by default. The token copies the named metadata from the launcher, adds `launched_by`, enrolls once within
5 minutes, and gives certificates of at most an hour, renewed only while the launcher is certified. `{}` turns
launching off. This deployment's runtimes and sandboxes are started by Compose, and don't launch.

## 8. Deployments

- **One machine:** the `identities` step fills each node's identity folder before any node starts: the roots,
  `issuer:8004 issuer` as where to enroll, and a token of the folder's kind (`sandbox-2` is a sandbox) for 7-day
  certificates. A token made stays.
- **Kubernetes:** the issuer a StatefulSet, each with a volume of its own for its intermediate; API servers a
  Deployment; runtimes a StatefulSet, each with a stable name for `AI_RUNTIME_ADDRESS` behind a headless Service, its
  pod open on 8004 and 8005; sandboxes each with a name of its own for `AI_SANDBOX_ADDRESS`. A Service passes TCP
  through to 8004; an ingress is for people's port only. NetworkPolicies keep sandboxes to their runtime and the
  issuer. An identity folder is writable, an emptyDir with `roots.pem`, `enroll` and the token copied in from
  Secrets; a new pod comes back with a new id.
- **Several sites:** the issuer and the API servers in one place, runtimes and sandboxes elsewhere. Addresses are
  public names behind a load balancer that passes TCP through, or a firewall open on 8004 to known sites. Each site's
  nodes get tokens of their own, labelled with the site. A runtime is reached at its `AI_RUNTIME_ADDRESS` by the API
  servers and by its sandboxes; sandboxes reach the issuer themselves. A site cut off keeps working until its
  certificates end.
