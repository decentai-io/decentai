# Roles and policies

What a person may do is never written on the person. It comes from the groups they are in, the roles those groups carry, and the policies those roles bundle. This chapter is that chain, the pages it is managed on, and how sharing sits beside it.

## The model

An identity is never a bag of permissions. It is a chain, walked on every request:

```
user → groups → roles → policies → statements
```

A **policy** is a list of statements, each allowing named actions. A **role** bundles policies. A **group** carries roles. A **user** belongs to groups. Nothing about authority is copied into a session, so a change takes effect on the next request, not the next sign-in.

Two rules hold everywhere:

- **An action is an endpoint.** Every operation the platform exposes is named by itself — `ai:chat:list`, `data:record:create`, `agents:agent:install` — and that name is what a policy grants. There is no separate permission vocabulary to keep in step.
- **Nothing is permitted by default.** A request is refused unless some statement reachable from the caller allows it.

## Groups, roles, policies

Build from the bottom: write a policy naming actions from the catalog, put it in a role, put the role in a group, put people in the group. The catalog on the policy page lists every action with a sentence saying what it is.

Actions worth knowing when writing a policy:

| Action | Grants |
|---|---|
| `agents:agent:install` | Installing and updating agents |
| `agents:agent:sourcecreate`, `sourceupdate`, `sourcerefresh`, `sourcedelete` | Adding, changing, refreshing and removing the sources agents are installed from |
| `agents:agent:grant` | Making an installed agent usable by people |
| `secrets:secret:set_owner_any` | Sharing credentials organization-wide and maintaining others' |
| `data:record:set_owner_any` | The same for records |
| `ai:audit:list_all` | Reading the organization's whole audit trail |
| `agents:monitor:usage`, `agents:monitor:events`, `agents:monitor:files` | Seeing what agents use and what was seen of them, on **Organization → Monitoring** |
| `settings:llm:create` | Adding language-model connections |
| `skills:skill:create` | Writing skills, private until shared |
| `skills:skill:set_owner_any` | Sharing skills organization-wide and maintaining others' |
| `settings:llm:manage_any`, `agents:agent:source_manage_any` | Seeing and maintaining every model connection, and every agent source, of the organization |

## Sharing

Every stored thing — a credential, a record, a file, a skill, a model connection, an agent source — follows one rule: private to its creator until shared; shareable with groups the sharer is in and with people they share a group with; editable only by its creator unless the domain's escape grant is held. Sharing with the whole organization needs that grant for credentials, records, files and skills, and is open to the creator for model connections and agent sources, which exist to be used.

The escape grant for a credential, a record, a file or a skill reaches what its holder can already see; the two for model connections and agent sources reach every one, since an organization depends on them. Seeing something is never authority over it. Its creator, or a holder of that grant, may **hand it over** to another member, who becomes its owner with the sharing untouched.
