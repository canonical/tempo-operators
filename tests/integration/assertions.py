from tests.integration.conftest import ALL_WORKERS, WORKER_APP, TEMPO_APP
from tests.integration.helpers import get_unit_ip_address

from jubilant import Juju
import requests


@retry(stop=stop_after_delay(2000), wait=wait_fixed(10))  # noqa: F821
def assert_charm_traces_ingested(deployment:Juju, tls:bool, distributed:bool):
    """Verify charm tracing for all tempo components."""
    # get tempo's IP
    tempo_ip = get_unit_ip_address(deployment, TEMPO_APP, 0)
    # query the tempo HTTP API for all juju_applications currently sending traces
    application_tags = requests.get(
        f"http{'s' if tls else ''}://{tempo_ip}:3200/api/search/tag/juju_application/values",
        verify=False,
    ).json()

    # verify tempo coordinator and all workers are in there
    apps = {TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,))}
    assert apps.issubset(application_tags["tagValues"]), application_tags["tagValues"]
