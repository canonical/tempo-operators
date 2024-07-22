import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from helpers import (
    deploy_cluster,
    emit_trace,
    get_application_ip,
    get_traces,
    get_traces_patiently,
)
from juju.application import Application
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"
logger = logging.getLogger(__name__)


async def get_tempo_traces_internal_endpoint(ops_test: OpsTest, proto="http"):
    hostname = f"{APP_NAME}-0.{APP_NAME}-endpoints.{ops_test.model.name}.svc.cluster.local"
    port = "4317"
    endpoint_postfix = ""
    if proto == "http":
        hostname = f"https://{hostname}"
        port = "4318"
        endpoint_postfix = "/v1/traces"
    return f"{hostname}:{port}{endpoint_postfix}"


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    tempo_charm = await ops_test.build_charm(".")
    resources = {
        "nginx-image": METADATA["resources"]["nginx-image"]["upstream-source"],
        "nginx-prometheus-exporter-image": METADATA["resources"][
            "nginx-prometheus-exporter-image"
        ]["upstream-source"],
    }
    await asyncio.gather(
        ops_test.model.deploy(tempo_charm, resources=resources, application_name=APP_NAME),
        ops_test.model.deploy(SSC, application_name=SSC_APP_NAME),
    )

    # deploy cluster
    await deploy_cluster(ops_test)

    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME, SSC_APP_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=10000,
            raise_on_error=False,
        ),
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    await ops_test.model.integrate(APP_NAME + ":certificates", SSC_APP_NAME + ":certificates")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, SSC_APP_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_push_tracegen_script_and_deps(ops_test: OpsTest):
    await ops_test.juju("scp", TRACEGEN_SCRIPT_PATH, f"{APP_NAME}/0:tracegen.py")
    await ops_test.juju(
        "ssh",
        f"{APP_NAME}/0",
        "python3 -m pip install opentelemetry-exporter-otlp-proto-grpc opentelemetry-exporter-otlp-proto-http",
    )


async def test_verify_trace_http_no_tls_fails(ops_test: OpsTest, server_cert, nonce):
    # IF tempo is related to SSC
    # WHEN we emit an http trace, **unsecured**
    tempo_endpoint = await get_tempo_traces_internal_endpoint(ops_test)
    await emit_trace(tempo_endpoint, ops_test, nonce=nonce)  # this should fail
    # THEN we can verify it's not been ingested
    traces = get_traces(await get_application_ip(ops_test, APP_NAME))
    assert len(traces) == 0


@pytest.mark.abort_on_fail
async def test_verify_trace_http_tls(ops_test: OpsTest, nonce, server_cert):
    # WHEN we emit a trace secured with TLS
    tempo_endpoint = await get_tempo_traces_internal_endpoint(ops_test)
    await emit_trace(tempo_endpoint, ops_test, nonce=nonce, use_cert=True)
    # THEN we can verify it's eventually ingested
    await get_traces_patiently(await get_application_ip(ops_test, APP_NAME))


@pytest.mark.abort_on_fail
async def test_verify_traces_grpc_tls(ops_test: OpsTest, nonce, server_cert):
    # enable otlp grpc receiver
    tempo_app: Application = ops_test.model.applications[APP_NAME]
    await tempo_app.set_config(
        {
            "always_enable_otlp_grpc": "True",
        }
    )
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="active",
        timeout=1000,
    )

    tempo_endpoint = await get_tempo_traces_internal_endpoint(ops_test, proto="grpc")
    # WHEN we emit a trace secured with TLS
    await emit_trace(tempo_endpoint, ops_test, nonce=nonce, verbose=1, proto="grpc", use_cert=True)
    # THEN we can verify it's been ingested
    await get_traces_patiently(await get_application_ip(ops_test, APP_NAME), service_name="grpc")


@pytest.mark.teardown
@pytest.mark.abort_on_fail
async def test_remove_relation(ops_test: OpsTest):
    await ops_test.juju(
        "remove-relation", APP_NAME + ":certificates", SSC_APP_NAME + ":certificates"
    )
    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=1000
        ),
    )
