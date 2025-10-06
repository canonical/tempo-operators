# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import pytest
from jubilant import Juju, all_active
from helpers import (
    deploy_monolithic_cluster,
    PROMETHEUS_APP,
    deploy_prometheus,
    INTEGRATION_TESTERS_CHANNEL,
    TEMPO_APP,
    get_unit_ip_address,
)
import requests

LOKI_APP = "loki"
GRAFANA_APP = "grafana"


@pytest.fixture(scope="module")
def grafana_admin_creds(juju) -> str:
    # NB this fixture can only be accessed after GRAFANA has been deployed.
    # obtain admin credentials via juju action, formatted as "username:password" (for basicauth)
    result = juju.run(GRAFANA_APP + "/0", "get-admin-password")
    return f"admin:{result.results['admin-password']}"


@pytest.fixture(scope="module")
def grafana_address(juju):
    return get_unit_ip_address(juju, GRAFANA_APP, 0)


@pytest.fixture(scope="module")
def datasources(grafana_admin_creds, grafana_address):
    res = requests.get(
        f"http://{grafana_admin_creds}@{grafana_address}:3000/api/datasources"
    )
    return res.json()


@pytest.fixture(scope="module")
def tempo_datasource(datasources) -> str:
    for ds in datasources:
        if ds["type"] == "tempo":
            return ds["name"]
    raise ValueError(f"No tempo datasource found in {repr(datasources)}")


@pytest.fixture(scope="module")
def loki_datasource(datasources) -> str:
    for ds in datasources:
        if ds["type"] == "loki":
            return ds["name"]
    raise ValueError(f"No loki datasource found in {repr(datasources)}")


@pytest.fixture(scope="module")
def prometheus_datasource(datasources) -> str:
    for ds in datasources:
        if ds["type"] == "prometheus":
            return ds["name"]
    raise ValueError(f"No prometheus datasource found in {repr(datasources)}")


@pytest.mark.setup
def test_setup(juju: Juju):
    # deploy tempo cluster
    deploy_monolithic_cluster(juju, wait_for_idle=False)
    # for service graphs
    deploy_prometheus(juju)
    # for traces-to-logs
    juju.deploy(
        "loki-k8s",
        app=LOKI_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )
    juju.deploy(
        "grafana-k8s",
        app=GRAFANA_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )
    juju.integrate(f"{TEMPO_APP}:grafana-source", GRAFANA_APP)
    juju.integrate(f"{LOKI_APP}:grafana-source", GRAFANA_APP)
    juju.integrate(f"{PROMETHEUS_APP}:grafana-source", GRAFANA_APP)

    juju.integrate(f"{TEMPO_APP}:send-remote-write", PROMETHEUS_APP)
    juju.integrate(f"{TEMPO_APP}:logging", LOKI_APP)

    juju.integrate(f"{TEMPO_APP}:receive-datasource", PROMETHEUS_APP)
    juju.integrate(f"{TEMPO_APP}:receive-datasource", LOKI_APP)

    juju.wait(
        all_active,
        timeout=2000,
        delay=10,
        successes=6,
    )


def test_service_graph_enabled(
    grafana_admin_creds,
    grafana_address,
    tempo_datasource,
    prometheus_datasource,
):
    url = f"http://{grafana_admin_creds}@{grafana_address}:3000/api/datasources/name/{tempo_datasource}"
    try:
        response = requests.get(url)
        data = response.json()
        assert "jsonData" in data
        assert "serviceMap" in data["jsonData"], f"service graph is not enabled"
        assert data["jsonData"]["serviceMap"]["datasourceUid"] == prometheus_datasource
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Request to Grafana failed: {e}")


def test_traces_to_logs_enabled(
    grafana_admin_creds, grafana_address, tempo_datasource, loki_datasource
):
    url = f"http://{grafana_admin_creds}@{grafana_address}:3000/api/datasources/name/{tempo_datasource}"
    try:
        response = requests.get(url)
        data = response.json()
        assert "jsonData" in data
        assert "tracesToLogsV2" in data["jsonData"], f"traces-to-logs is not enabled"
        assert data["jsonData"]["tracesToLogsV2"]["datasourceUid"] == loki_datasource
    except requests.exceptions.RequestException as e:
        pytest.fail(f"Request to Grafana failed: {e}")
