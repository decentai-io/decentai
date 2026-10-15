"""Move uploaded files and approved agent code to the store
FILE_STORAGE_PROVIDER names (docs/run/operating.md, "Moving the files").

Every file whose record says its bytes are elsewhere is copied, read
back, and its record pointed at the new store; every approved package
in the old store is copied over. Nothing is deleted where it was: run
it again and it finds nothing left, then empty the old store by hand
once the deployment has been seen working on the new one. A file whose
old store is not configured any more is reported, not lost.

    python bootstrap/move_storage.py                          # move
    python bootstrap/move_storage.py --check                  # count only
    python bootstrap/move_storage.py --packages-from s3       # packages
                                         # were in another cloud, not on disk

Run where the backend runs, with its settings and the old store's still
set, and with the backend stopped or idle.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_ROOT = PROJECT_ROOT / "backend"
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(BACKEND_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(BACKEND_ROOT / "config.env", override=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true",
                        help="report what would move without writing")
    parser.add_argument("--packages-from", default="local",
                        help="where approved packages are now (default: local)")
    arguments = parser.parse_args()

    from database import MongoDB
    from database.crypto import SecretCipher
    from database.storage_move import move
    from server.setup.app_settings import Settings
    from server.setup.app_state import get_db, get_state

    state = get_state()
    state.settings = Settings.from_env()
    state.db = MongoDB(state.settings)
    SecretCipher.verify()

    target = state.settings.file_storage_provider
    moved = move(get_db(), state.settings, check=arguments.check,
                 packages_from=arguments.packages_from)
    verb = "would move" if arguments.check else "moved"
    print(f"files: {verb} {moved.files} to {target}; "
          f"{moved.files_already} already there")
    print(f"packages: {verb} {moved.packages} from {arguments.packages_from} "
          f"to {target}; {moved.packages_already} already there")
    for problem in moved.problems:
        print(f"  NOT MOVED — {problem}", file=sys.stderr)
    if moved.problems:
        print(f"\n{len(moved.problems)} could not be moved. They are where "
              f"they were and still read from there.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
