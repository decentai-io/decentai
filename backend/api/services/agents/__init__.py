"""Fetching agent code — the supply half of the agents domain.

    sources.py      a repository, opened at one commit
    repository.py   the git mechanics: fetch, catalog, safety rails
    git_askpass.py  ...answering git's credential prompt, out of process
    acquisition.py  what is done with an open source: inspect, package,
                    keep, reclaim

This service reads repositories and produces packages; it holds no
state of its own. What it PRODUCES is kept elsewhere, by the layering
rule everything else follows: the stored bytes live in
`database/agent_packages` (beside `database/file_connectors`, the same
arrangement), and the approvals that name them live in
`database/stores/agents`.

One door reaches these, and it is not a chat: the agent controller,
when someone installs, updates or refreshes a source.

Nothing here runs agent code. Cloning, reading a manifest and packaging
a folder all happen without importing a line of it; importing is the
runtime's job, and only after an approval.
"""
