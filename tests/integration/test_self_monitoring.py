import requests
from jubilant import Juju
from tenacity import stop_after_delay, wait_fixed, retry

from tests.integration.conftest import TEMPO_APP, ALL_WORKERS, WORKER_APP
from tests.integration.helpers import get_unit_ip_address


@retry(stop=stop_after_delay(2000), wait=wait_fixed(10))  # noqa: F821
def test_self_monitoring_charm_tracing(deployment: Juju, distributed, tls):
    tempo_ip = get_unit_ip_address(deployment, TEMPO_APP, 0)
    application_tags = requests.get(
        f"http{'s' if tls else ''}://{tempo_ip}:3200/api/search/tag/juju_application/values",
        verify=False,
    ).json()

    apps = {TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,))}
    assert apps.issubset(application_tags["tagValues"]), application_tags["tagValues"]
