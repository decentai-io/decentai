"""Generate (or rotate) the platform's secret-encryption keys.

Fresh pair — paste both lines into backend/config.env (or the
deployment's environment):

    python bootstrap/generate_secret_keys.py

Rotation — pass the CURRENT map; a new version is appended and made
active (restart the backend, then run bootstrap/reencrypt_secrets.py to
move stored values onto the new key):

    python bootstrap/generate_secret_keys.py --rotate "01:<key>,02:<key>"

Versions are zero-padded two-digit numbers: the active key defaults to
the lexicographically highest version, so 10 must sort above 09.
Nothing is written to disk.
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "backend"))

from database.crypto import SecretCipher  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--rotate", metavar="CURRENT_MAP",
        help="the current SECRET_ENCRYPTION_KEYS value to append to",
    )
    arguments = parser.parse_args()

    if arguments.rotate:
        entries = [
            entry.strip() for entry in arguments.rotate.split(",")
            if entry.strip()
        ]
        versions = []
        for entry in entries:
            version, _, material = entry.partition(":")
            if not version.strip() or not material.strip():
                print(
                    "ERROR: --rotate expects the current map, e.g. "
                    "'01:<key>,02:<key>'.", file=sys.stderr,
                )
                return 1
            versions.append(version.strip())
        numeric = [int(v) for v in versions if v.isdigit()]
        next_version = f"{(max(numeric) if numeric else 0) + 1:02d}"
        entries.append(f"{next_version}:{SecretCipher.new_key()}")
        print(f"SECRET_ENCRYPTION_KEYS={','.join(entries)}")
        print(f"SECRET_ENCRYPTION_ACTIVE={next_version}")
        return 0

    print(f"SECRET_ENCRYPTION_KEYS=01:{SecretCipher.new_key()}")
    print("SECRET_ENCRYPTION_ACTIVE=01")
    return 0


if __name__ == "__main__":
    sys.exit(main())
