"""Standing an organization up — kept with the Data Core.

The provisioner lives in ``data_core/server/authentication/provisioning.py``,
because the Data Core's first-run and sign-up pages stand organizations up
too. The scripts beside this one import it from here, with the Data Core
on their path, as they always have.
"""

from server.authentication.provisioning import OrganizationProvisioner

__all__ = ["OrganizationProvisioner"]
