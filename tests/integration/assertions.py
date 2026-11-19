import time

import requests
from jubilant import Juju
from tenacity import retry, retry_if_exception_type, stop_after_delay, wait_fixed

from tests.integration.helpers import (
    ALL_WORKERS,
    TEMPO_APP,
    WORKER_APP,
    get_unit_ip_address,
)


@retry(stop=stop_after_delay(300), wait=wait_fixed(3))
def _wait_until_serving(tempo_ip: str, scheme: str) -> None:
    """Wait until Tempo is serving over the expected scheme."""
    requests.get(
        f"{scheme}://{tempo_ip}:3200/ready", verify=False, timeout=3
    ).raise_for_status()


@retry(
    stop=stop_after_delay(2000),
    wait=wait_fixed(10),
    # retry only "not there yet" (no traces); a bug should fail at once
    retry=retry_if_exception_type((AssertionError, requests.RequestException)),
    reraise=True,
)
def _assert_traces_since(
    tempo_ip: str, scheme: str, distributed: bool, since: float
) -> None:
    """Verify that all expected Tempo components have emitted charm traces since `since`."""
    # verify tempo coordinator and all workers are in there
    apps = {TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,))}
    # query the tempo HTTP API for each juju_application currently sending traces
    for app in apps:
        resp = requests.get(
            f"{scheme}://{tempo_ip}:3200/api/search",
            params={
                "tags": f"juju_application={app}",
                "start": int(since),
                "end": int(since) + 7200,  # Tempo requires both start and end
            },
            verify=False,
            timeout=10,
        )
        resp.raise_for_status()
        assert resp.json().get("traces"), f"no charm traces for {app!r} since {since}"


def assert_charm_traces_ingested(deployment: Juju, tls: bool, distributed: bool) -> None:
    """Verify charm tracing for all tempo components."""
    scheme = "https" if tls else "http"
    # get tempo's IP
    tempo_ip = get_unit_ip_address(deployment, TEMPO_APP, 0)
    _wait_until_serving(tempo_ip, scheme)
    _assert_traces_since(tempo_ip, scheme, distributed, since=time.time())
