#!/usr/bin/env python3
"""What git calls when a private repository asks who we are.

Reached through the small launcher Repository._askpass writes, because
git executes GIT_ASKPASS as a program and a .py file is not one. The
shebang means it also runs directly wherever python3 is on PATH.

Using askpass keeps the credential out of the command line (and so out of
the process table) and out of the remote URL. git runs this once for the
username and once for the password, distinguishing them by the prompt it
passes as the first argument.
"""

import os
import sys


def main() -> int:
    prompt = (sys.argv[1] if len(sys.argv) > 1 else "").lower()
    if "username" in prompt:
        print(os.environ.get("DECENTAI_GIT_USERNAME", ""))
    else:
        print(os.environ.get("DECENTAI_GIT_TOKEN", ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
