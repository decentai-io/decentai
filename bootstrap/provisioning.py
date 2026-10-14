"""Standing an organization up — kept with the backend.

The provisioner lives in ``backend/server/authentication/provisioning.py``,
because the backend's first-run and sign-up pages stand organizations up
too. The scripts beside this one import it from here, with the backend
on their path, as they always have.
"""

from server.authentication.provisioning import OrganizationProvisioner

__all__ = ["OrganizationProvisioner"]
