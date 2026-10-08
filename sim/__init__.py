"""The platform, simulated in memory — the runtime's reference services.

The runtime speaks one services contract (ai_runtime/chat/session.py,
docs/reference/session-door.md): state, messages, the inbox, approvals, storage,
skills, memories, schedules, the chat's contract, and a resource
provider. The backend implements it over its gateway; this package
implements it in memory, and is what the runtime runs against when no
backend is configured (ai_runtime/main.py) and what its whole test suite
uses, so the runtime is exercised end to end without a database:

    session_services.py   SimSessionServices — the contract, in dicts
    resources.py          InMemoryResourceProvider — data, files, secrets
    schedules.py          MemoryScheduleStore — the clock's rows, in memory
    mcp_server.py         FakeMcpServer — a stand-in MCP server, for tests

Everything here honours the same rules the backend does — a schedule
row's shape, an approval that lands once, events durable before they
are absorbed — because the tests that run against it are the tests that
pin the runtime's behaviour, and a simulation that were kinder than the
backend would pin the wrong thing.
"""
