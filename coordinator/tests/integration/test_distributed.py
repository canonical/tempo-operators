import shlex
import subprocess
from pathlib import Path

import pytest
from jubilant import Juju

from helpers import (
    TEMPO_APP,
    deploy_distributed_cluster,
    emit_trace,
    get_tempo_application_endpoint,
    get_app_ip_address,
    ALL_ROLES,
    ALL_WORKERS,
)
from tempo import Tempo
from tests.integration.helpers import get_traces_patiently


@pytest.mark.setup
def test_deploy_tempo_distributed(juju: Juju, tempo_charm: Path):
    deploy_distributed_cluster(juju, ALL_ROLES)


# TODO: could extend with optional protocols and always-enable them as needed
def test_trace_ingestion(juju):
    # WHEN we emit a trace
    tempo_address = get_app_ip_address(juju, TEMPO_APP)
    tempo_ingestion_endpoint = get_tempo_application_endpoint(
        tempo_address, protocol="otlp_http", tls=False
    )
    emit_trace(tempo_ingestion_endpoint, juju)

    # THEN we can verify it's been ingested
    get_traces_patiently(
        tempo_address,
        tls=False,
    )


def get_metrics(ip: str, port: int):
    proc = subprocess.run(
        shlex.split(f"curl {ip}:{port}/metrics"), text=True, capture_output=True
    )
    return proc.stdout


def test_metrics_endpoints(juju):
    # verify that all worker apps and the coordinator can be scraped for metrics on their application IP
    for app in (*ALL_WORKERS, TEMPO_APP):
        app_ip = get_app_ip_address(juju, app)
        assert get_metrics(app_ip, port=Tempo.tempo_http_server_port)


@pytest.mark.teardown
def test_teardown(juju: Juju):
    for worker_name in ALL_WORKERS:
        juju.remove_application(worker_name)
    juju.remove_application(TEMPO_APP)
