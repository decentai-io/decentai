"""The sandbox: programs run for its callers, each process jailed, its messages relayed (documentation/sandbox.md).

    sandbox.py     the sandbox: its processes started and ended, its state, its stopping
    callers.py     its callers: told it is there, called back, and the orders they give
    process.py     one process: its messages both ways, and its end
    files.py       files in and out of a process's home
    programs.py    the programs it holds, checked and unpacked once
    jail.py        the jail and the cgroup every process runs in
    way_out.py     a process's one way out
    watch.py       what the processes use; the machine kept from running out
"""
