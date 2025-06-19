import logging

from jubilant import Juju

# Application names used uniformly across the tests

logger = logging.getLogger(__name__)


def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address
