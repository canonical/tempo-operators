import asyncio
import logging
import shlex
import subprocess
from pathlib import Path

import pytest
from helpers import (
    APP_NAME,
    WORKER_NAME,
    deploy_distributed_cluster,
    emit_trace,
    get_application_ip,
    get_tempo_application_endpoint,
)
from pytest_operator.plugin import OpsTest

from tempo import Tempo
from tempo_config import TempoRole
from tests.integration.helpers import (
    get_resources,
    get_traces_patiently,
)

# FIXME: metrics-generator  goes to error state
# https://github.com/canonical/tempo-worker-k8s-operator/issues/61
ALL_ROLES = [role for role in TempoRole.all_nonmeta() if role != "metrics-generator"]
ALL_WORKERS = [f"{WORKER_NAME}-" + role for role in ALL_ROLES]
S3_INTEGRATOR = "s3-integrator"

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_deploy_tempo_distributed(ops_test: OpsTest, tempo_charm: Path):
    await ops_test.model.deploy(
        tempo_charm, resources=get_resources("."), application_name=APP_NAME, trust=True
    )
    await deploy_distributed_cluster(ops_test, ALL_ROLES)


# TODO: could extend with optional protocols and always-enable them as needed
async def test_trace_ingestion(ops_test):
    # WHEN we emit a trace
    tempo_address = await get_application_ip(ops_test, APP_NAME)
    tempo_ingestion_endpoint = await get_tempo_application_endpoint(
        tempo_address, protocol="otlp_http", tls=False
    )
    await emit_trace(tempo_ingestion_endpoint, ops_test)

    # THEN we can verify it's been ingested
    await get_traces_patiently(
        tempo_address,
        tls=False,
    )


def get_metrics(ip: str, port: int):
    proc = subprocess.run(shlex.split(f"curl {ip}:{port}/metrics"), text=True, capture_output=True)
    return proc.stdout


async def test_metrics_endpoints(ops_test):
    # verify that all worker apps and the coordinator can be scraped for metrics on their application IP
    for app in (*ALL_WORKERS, APP_NAME):
        app_ip = await get_application_ip(ops_test, app)
        assert get_metrics(app_ip, port=Tempo.tempo_http_server_port)


@pytest.mark.teardown
async def test_teardown(ops_test: OpsTest):
    await asyncio.gather(
        *(ops_test.model.remove_application(worker_name) for worker_name in ALL_WORKERS),
        ops_test.model.remove_application(APP_NAME),
    )
