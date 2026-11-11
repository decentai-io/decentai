#!/bin/sh
# The suite, in the runner: the packages laid out as the image lays them out, then pytest once.
set -e
mkdir -p /work/shared
ln -sfn /src/api_server/src /work/api_server
ln -sfn /src/communication /work/communication
for part in __init__.py cron.py file_types.py rules.py llm; do ln -sfn "/src/shared/$part" "/work/shared/$part"; done
# Apart: a list with hashes holds every package of its install to them.
pip install -q --root-user-action=ignore -r /src/api_server/requirements.txt 2>&1 | grep -v notice || true
pip install -q --root-user-action=ignore pytest pytest-timeout 2>&1 | grep -v notice || true
cd /work && PYTHONPATH=/work PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider --timeout=60 \
    /src/api_server/tests "$@"
