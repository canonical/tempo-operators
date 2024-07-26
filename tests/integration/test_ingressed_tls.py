import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from helpers import deploy_cluster, emit_trace, get_traces_patiently
from juju.application import Application
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
WORKER_NAME = "tempo-worker"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"
TRAEFIK = "traefik-k8s"
TRAEFIK_APP_NAME = "trfk"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

logger = logging.getLogger(__name__)


async def get_ingress_proxied_endpoint(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    app = status["applications"][TRAEFIK_APP_NAME]
    status_msg = app["status"]["info"]

    # hacky way to get ingress hostname
    if "Serving at" not in status_msg:
        assert False, f"Ingressed hostname is not present in {TRAEFIK_APP_NAME} status message."
    return status_msg.replace("Serving at", "").strip()


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
        ops_test.model.deploy(TRAEFIK, application_name=TRAEFIK_APP_NAME, channel="edge"),
    )

    # deploy cluster
    await deploy_cluster(ops_test)

    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=10000,
            raise_on_error=False,
        ),
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


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    await ops_test.model.integrate(APP_NAME + ":certificates", SSC_APP_NAME + ":certificates")
    await ops_test.model.integrate(
        SSC_APP_NAME + ":certificates", TRAEFIK_APP_NAME + ":certificates"
    )
    await ops_test.model.integrate(APP_NAME + ":ingress", TRAEFIK_APP_NAME + ":traefik-route")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME, WORKER_NAME],
        status="active",
        timeout=1000,
        # make idle period 1 minute, as Tempo workload might not be up yet
        idle_period=60,
    )


# TODO: Uncomment and fix once below issue is fixed
# Currently, traefik, through `traefik_route`, accepts traffic through http although TLS is enabled.
# @pytest.mark.abort_on_fail
# async def test_verify_ingressed_trace_http_upgrades_to_tls(ops_test: OpsTest, nonce):
#     tempo_host = await get_tempo_host(ops_test)
#     # IF tempo is related to SSC
#     # WHEN we emit an http trace, **unsecured**
#     await emit_trace(
#         f"http://{tempo_host}:4318/v1/traces", nonce=nonce, ops_test=ops_test
#     )  # this should fail
#     # THEN we can verify it's not been ingested
#     assert get_traces_patiently(tempo_host, nonce=nonce)


@pytest.mark.abort_on_fail
async def test_verify_ingressed_trace_http_tls(ops_test: OpsTest, nonce, server_cert):
    tempo_host = await get_ingress_proxied_endpoint(ops_test)

    await emit_trace(
        f"https://{tempo_host}:4318/v1/traces", nonce=nonce, ops_test=ops_test, use_cert=True
    )
    # THEN we can verify it's been ingested
    assert await get_traces_patiently(tempo_host)


@pytest.mark.abort_on_fail
async def test_verify_ingressed_traces_grpc_tls(ops_test: OpsTest, nonce, server_cert):
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

    tempo_host = await get_ingress_proxied_endpoint(ops_test)

    await emit_trace(
        f"{tempo_host}:4317", nonce=nonce, proto="grpc", ops_test=ops_test, use_cert=True
    )
    # THEN we can verify it's been ingested
    assert await get_traces_patiently(tempo_host, service_name="tracegen-grpc")


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
