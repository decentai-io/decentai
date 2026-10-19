"""Shared plumbing for IAM controllers. Authorization is NOT done here —
the router already checked the endpoint. Controllers keep only the
invariants on what a permitted action may do: grant boundary, lockout check.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

from server.authentication.policy import PolicyEngine
from server.custom_logging import CustomLoggerFactory


class IAMController:
    def __init__(self):
        self.policy = PolicyEngine()
        self.logger = CustomLoggerFactory.get_logger(self.__class__.__name__)

    @staticmethod
    def _payload(data: dict) -> Dict[str, Any]:
        inner = (data or {}).get("data")
        return inner if isinstance(inner, dict) else {}

    @staticmethod
    def _org(user: Dict[str, Any]) -> str:
        """The caller's organization, from their own record.

        Never from the request: a parameter is something a caller can
        lie about, and this is the whole of the tenant boundary.
        """
        return str((user or {}).get("org_id") or "")

    def _lockout_check(self, org_id: str, undo) -> Optional[Tuple[Dict[str, Any], int]]:
        """Run after a mutation that reshapes the access chain. When it left
        nobody able to manage access, ``undo`` reverts it and the caller
        returns the refusal. None when the invariant still holds."""
        if self.policy.someone_can_manage_access(org_id):
            return None
        undo()
        return {
            "error": "Rejected: this change would leave nobody able to "
                     "manage access."
        }, 409
