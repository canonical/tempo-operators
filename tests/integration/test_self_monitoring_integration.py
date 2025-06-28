import requests
from jubilant import Juju, all_active, any_error
from tenacity import stop_after_delay, wait_fixed, retry

from tests.integration.conftest import (
    TEMPO_APP,
    ALL_WORKERS,
    WORKER_APP,
    LOKI_APP,
    PROMETHEUS_APP,
    SSC_APP,
)
from tests.integration.helpers import (
    get_unit_ip_address,
    get_traces_patiently,
    get_tempo_hostname,
    deploy_prometheus,
    deploy_loki,
)
import pytest
from tenacity import stop_after_attempt


@pytest.mark.setup
def test_deploy_self_monitoring_stack(juju: Juju):
    # GIVEN a model with pyroscope cluster
    # WHEN we deploy a monitoring stack
    deploy_prometheus(juju)
    deploy_loki(juju)
    # THEN monitoring stack is in active/idle state
    juju.wait(
        lambda status: all_active(status, PROMETHEUS_APP, LOKI_APP),
        error=any_error,
        timeout=2000,
    )


@pytest.mark.setup
def test_relate_self_monitoring_stack(juju: Juju, workers, tls):
    # GIVEN a model with a pyroscope cluster, and a monitoring stack
    # WHEN we integrate the pyroscope cluster over self-monitoring relations
    juju.integrate(
        TEMPO_APP + ":metrics-endpoint", PROMETHEUS_APP + ":metrics-endpoint"
    )
    juju.integrate(TEMPO_APP + ":logging", LOKI_APP + ":logging")

    if tls:
        juju.integrate(PROMETHEUS_APP + ":certificates", SSC_APP)

    # THEN the coordinator, all workers, and the monitoring stack are all in active/idle state
    juju.wait(
        lambda status: all_active(
            status, PROMETHEUS_APP, LOKI_APP, TEMPO_APP, *workers
        ),
        error=any_error,
        timeout=2000,
        delay=5,
        successes=12,
    )


@retry(stop=stop_after_attempt(5), wait=wait_fixed(10))
def test_metrics(juju: Juju, workers):
    # GIVEN a pyroscope cluster integrated with prometheus over metrics-endpoint
    address = get_unit_ip_address(juju, PROMETHEUS_APP, 0)
    # WHEN we query the metrics for the coordinator and each of the workers
    url = f"http://{address}:9090/api/v1/query"
    for app in (TEMPO_APP, *workers):
        params = {"query": f"up{{juju_application='{app}'}}"}
        # THEN we should get a successful response and at least one result
        try:
            response = requests.get(url, params=params)
            data = response.json()
            assert data["status"] == "success", f"Metrics query failed for app '{app}'"
            assert len(data["data"]["result"]) > 0, f"No metrics found for app '{app}'"
        except requests.exceptions.RequestException as e:
            assert False, f"Request to Prometheus failed for app '{app}': {e}"


@retry(stop=stop_after_attempt(5), wait=wait_fixed(10))
def test_logs(juju: Juju, workers):
    # GIVEN a pyroscope cluster integrated with loki over logging
    address = get_unit_ip_address(juju, LOKI_APP, 0)
    # WHEN we query the logs for each worker
    # Use query_range for a longer default time interval
    url = f"http://{address}:3100/loki/api/v1/query_range"

    for app in workers:
        query = f'{{juju_application="{app}"}}'
        params = {"query": query}
        # THEN we should get a successful response and at least one result
        try:
            response = requests.get(url, params=params)
            data = response.json()
            assert data["status"] == "success", f"Log query failed for app '{app}'"
            assert len(data["data"]["result"]) > 0, f"No logs found for app '{app}'"
        except requests.exceptions.RequestException as e:
            assert False, f"Request to Loki failed for app '{app}': {e}"


@retry(stop=stop_after_delay(2000), wait=wait_fixed(10))  # noqa: F821
def test_charm_tracing(deployment: Juju, distributed, tls):
    tempo_ip = get_unit_ip_address(deployment, TEMPO_APP, 0)
    application_tags = requests.get(
        f"http{'s' if tls else ''}://{tempo_ip}:3200/api/search/tag/juju_application/values",
        verify=False,
    ).json()

    apps = {TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,))}
    assert apps.issubset(application_tags["tagValues"]), application_tags["tagValues"]


def test_workload_tracing(juju: Juju, ingress, tls):
    tempo_host = get_tempo_hostname(juju, ingress=ingress)
    # verify traces from tempo-scalable-single-binary are ingested
    assert get_traces_patiently(
        tempo_host, service_name="tempo-scalable-single-binary", tls=tls
    )
