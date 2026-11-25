import logging
import shlex
import subprocess

import pytest
from jubilant import Juju
from tenacity import before_sleep_log, retry, stop_after_attempt, wait_fixed

from tempo import Tempo
from tests.integration.helpers import (
    ALL_ROLES,
    ALL_WORKERS,
    TEMPO_APP,
    deploy_distributed_cluster,
    emit_trace,
    get_app_ip_address,
    get_tempo_application_endpoint,
    query_traces_patiently_from_client_localhost,
)

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy_tempo_distributed(juju: Juju):
    deploy_distributed_cluster(juju, ALL_ROLES)


# TODO: could extend with optional protocols and always-enable them as needed
def test_trace_ingestion(juju):
    # WHEN we emit a trace
    tempo_address = get_app_ip_address(juju, TEMPO_APP)
    tempo_ingestion_endpoint = get_tempo_application_endpoint(
        tempo_address, protocol="otlp_http", tls=False
    )
    emit_trace(tempo_ingestion_endpoint)

    # THEN we can verify it's been ingested
    query_traces_patiently_from_client_localhost(
        tempo_address,
        tls=False,
    )


# A worker pod can still be restarting (e.g. after a config update) when `juju.wait` returns,
# so poll until the endpoint serves metrics instead of scraping once.
@retry(
    stop=stop_after_attempt(60),
    wait=wait_fixed(5),
    before_sleep=before_sleep_log(logger, logging.INFO),
    reraise=True,
)
def get_metrics(ip: str, port: int):
    proc = subprocess.run(
        shlex.split(f"curl {ip}:{port}/metrics"), text=True, capture_output=True
    )
    assert proc.stdout, f"{ip}:{port} served no metrics"
    return proc.stdout


def test_metrics_endpoints(juju):
    # verify that all worker apps and the coordinator can be scraped for metrics on their application IP
    for app in (*ALL_WORKERS, TEMPO_APP):
        app_ip = get_app_ip_address(juju, app)
        get_metrics(app_ip, port=Tempo.tempo_http_server_port)


@pytest.mark.juju_teardown
def test_teardown(juju: Juju):
    for worker_name in ALL_WORKERS:
        juju.remove_application(worker_name)
    juju.remove_application(TEMPO_APP)
