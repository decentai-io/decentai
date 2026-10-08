"""Execution: what one organization may do with a package, enforced
per invocation.

Packages themselves — code, environments, workers — are
ai_runtime/agents/. This module is the judgment wrapped around them:

    grants.py     the delegation's function grants — deny by default
    executor.py   the one chokepoint every invocation passes through:
                  validate, authorize, pause for a human, then run it
                  in the agent's worker and believe nothing unchecked
    resources.py  the mediated resource API — bindings, not credentials
    pictures.py   a picture an agent hands over, checked before a model
                  or a person is shown it
    code_grant.py what a person allowed for one call that runs code

Reasoning about WHICH function to call is the chat's
(ai_runtime/reasoning/assistant.py); this module only ever judges and runs
one proposed invocation at a time.
"""

from ai_runtime.execution.executor import (
    FunctionExecutor,
    ParkedInvocation,
    action_hash,
)
from ai_runtime.execution.grants import FunctionGrants
from ai_runtime.execution.resources import ResourceAccess, ResourceDenied

__all__ = [
    "FunctionExecutor",
    "FunctionGrants",
    "ParkedInvocation",
    "ResourceAccess",
    "ResourceDenied",
    "action_hash",
]
