import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from helpers import (
    WORKER_NAME,
    deploy_monolithic_cluster,
    emit_trace,
    get_application_ip,
    get_tempo_ingressed_endpoint,
    get_tempo_internal_endpoint,
    get_traces,
    get_traces_patiently,
    protocols_endpoints,
)
from juju.application import Application
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
SSC = "self-signed-certificates"
SSC_APP_NAME = "ssc"
TRAEFIK = "traefik-k8s"
TRAEFIK_APP_NAME = "trfk"

logger = logging.getLogger(__name__)


async def get_ingress_proxied_hostname(ops_test: OpsTest):
    status = await ops_test.model.get_status()
    app = status["applications"][TRAEFIK_APP_NAME]
    status_msg = app["status"]["info"]

    # hacky way to get ingress hostname
    if "Serving at" not in status_msg:
        assert False, f"Ingressed hostname is not present in {TRAEFIK_APP_NAME} status message."
    return status_msg.replace("Serving at", "").strip()


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest, tempo_charm: Path):
    resources = {
        "nginx-image": METADATA["resources"]["nginx-image"]["upstream-source"],
        "nginx-prometheus-exporter-image": METADATA["resources"][
            "nginx-prometheus-exporter-image"
        ]["upstream-source"],
    }
    await asyncio.gather(
        ops_test.model.deploy(
            tempo_charm, resources=resources, application_name=APP_NAME, trust=True
        ),
        ops_test.model.deploy(SSC, application_name=SSC_APP_NAME),
        ops_test.model.deploy(
            TRAEFIK, application_name=TRAEFIK_APP_NAME, channel="edge", trust=True
        ),
    )

    await ops_test.model.integrate(
        SSC_APP_NAME + ":certificates", TRAEFIK_APP_NAME + ":certificates"
    )
    # deploy cluster
    await deploy_monolithic_cluster(ops_test)

    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME, WORKER_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=2000,
        ),
    )


@pytest.mark.abort_on_fail
async def test_relate_ssc(ops_test: OpsTest):
    await ops_test.model.integrate(APP_NAME + ":certificates", SSC_APP_NAME + ":certificates")
    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME, WORKER_NAME],
            status="active",
            raise_on_blocked=True,
            timeout=1000,
        ),
    )


async def test_verify_trace_http_no_tls_fails(ops_test: OpsTest, server_cert, nonce):
    # IF tempo is related to SSC
    # WHEN we emit an http trace, **unsecured**
    tempo_endpoint = get_tempo_internal_endpoint(ops_test, tls=False, protocol="otlp_http")
    await emit_trace(tempo_endpoint, ops_test, nonce=nonce)  # this should fail
    # THEN we can verify it's not been ingested
    traces = get_traces(await get_application_ip(ops_test, APP_NAME))
    assert len(traces) == 0


@pytest.mark.abort_on_fail
async def test_verify_traces_otlp_http_tls(ops_test: OpsTest, nonce):
    protocol = "otlp_http"
    svc_name = f"tracegen-{protocol}"
    tempo_endpoint = get_tempo_internal_endpoint(ops_test, protocol=protocol, tls=True)
    # WHEN we emit a trace secured with TLS
    await emit_trace(
        tempo_endpoint,
        ops_test,
        nonce=nonce,
        verbose=1,
        proto=protocol,
        use_cert=True,
        service_name=svc_name,
    )
    # THEN we can verify it's been ingested
    await get_traces_patiently(await get_application_ip(ops_test, APP_NAME), service_name=svc_name)


@pytest.mark.abort_on_fail
async def test_relate_ingress(ops_test: OpsTest):
    await ops_test.model.integrate(APP_NAME + ":ingress", TRAEFIK_APP_NAME + ":traefik-route")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, SSC_APP_NAME, TRAEFIK_APP_NAME, WORKER_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_force_enable_protocols(ops_test: OpsTest):
    tempo_app: Application = ops_test.model.applications[APP_NAME]
    config = {}
    for protocol in list(protocols_endpoints.keys()):
        config[f"always_enable_{protocol}"] = "True"

    await tempo_app.set_config(config)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, WORKER_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.parametrize("protocol", protocols_endpoints.keys())
async def test_verify_traces_force_enabled_protocols_tls(ops_test: OpsTest, nonce, protocol):
    tempo_host = await get_ingress_proxied_hostname(ops_test)
    logger.info(f"emitting & verifying trace using {protocol} protocol.")
    tempo_endpoint = get_tempo_ingressed_endpoint(tempo_host, protocol=protocol, tls=True)
    # emit a trace secured with TLS
    await emit_trace(
        tempo_endpoint,
        ops_test,
        nonce=nonce,
        verbose=1,
        proto=protocol,
        use_cert=True,
        service_name=f"tracegen-tls-{protocol}",
    )
    # verify it's been ingested
    await get_traces_patiently(tempo_host, service_name=f"tracegen-tls-{protocol}")


@pytest.mark.abort_on_fail
async def test_workload_traces_tls(ops_test: OpsTest):
    tempo_host = await get_ingress_proxied_hostname(ops_test)
    # verify traces from tempo-scalable-single-binary are ingested
    assert await get_traces_patiently(
        tempo_host,
        service_name="tempo-scalable-single-binary",
    )


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
