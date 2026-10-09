"""What runs in the agents' container (docs/system/sandbox.md).

The runtime's image, started a second time with another command. No
decision about an agent is made here, and nothing here imports the
runtime's side (``ai_runtime/agents``): the two meet on a socket and on
one volume.

    service.py        SpawnerService — the one process the platform
                      runs here: it starts what the runtime orders,
                      carries a worker's lines, passes workers'
                      connections to the runtime's proxy, and watches
                      what the agents use
    usage.py          what the container is given, what each agent
                      uses, and who is ended when it runs out
    spawn_helper.c    the one program that may start a process as
                      another user; every worker begins as it
    wire.py           what the two sides agree on: the messages, the
                      marks on a worker's lines, the key's file

Where no container is kept for agents — a developer's machine, the
tests — none of ``service.py`` runs: the runtime starts workers itself
(``agents/spawner.py``, ``LocalSpawner``), and the helper, where there
is one, runs beside it.
"""
