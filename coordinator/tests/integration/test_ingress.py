#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from contextlib import contextmanager
from pathlib import Path

import jubilant
import pytest
from jubilant import Juju, all_active

from helpers import (
    WORKER_APP,
    deploy_monolithic_cluster,
    emit_trace,
    get_tempo_ingressed_endpoint,
    query_traces_patiently_from_client_localhost,
    protocols_endpoints,
    TRAEFIK_APP,
    TEMPO_APP,
    get_ingress_proxied_hostname,
    get_istio_ingress_ip,
    ISTIO_APP,
    ISTIO_INGRESS_APP,
    INTEGRATION_TESTERS_CHANNEL,
)


logger = logging.getLogger(__name__)


@contextmanager
def traefik_ingress(juju: Juju, tempo_app_name: str = TEMPO_APP):
    """Deploy and configure Traefik ingress for Tempo.

    This context manager:
    1. Deploys traefik-k8s
    2. Integrates it with Tempo over the 'ingress' relation
    3. Waits for active/idle
    4. Yields control
    5. Removes traefik and waits for active/idle
    """
    # Deploy traefik
    juju.deploy(
        "traefik-k8s",
        app=TRAEFIK_APP,
        channel="edge",
        trust=True,
    )
    juju.wait(
        all_active,
        timeout=1000,
        delay=5,
        successes=5,
    )

    # Integrate with tempo
    juju.integrate(f"{TRAEFIK_APP}:traefik-route", f"{tempo_app_name}:ingress")
    juju.wait(
        all_active,
        timeout=2000,
        delay=5,
        successes=5,
    )

    yield

    # Cleanup
    juju.remove_application(TRAEFIK_APP, force=True, destroy_storage=True)
    juju.wait(
        all_active,
        timeout=1000,
        delay=5,
        successes=5,
    )


@contextmanager
def istio_ingress(juju: Juju, tempo_app_name: str = TEMPO_APP):
    """Deploy and configure Istio ingress for Tempo.

    This context manager:
    1. Deploys istio-k8s
    2. Deploys istio-ingress-k8s
    4. Integrates istio-ingress-k8s with Tempo over the 'istio-ingress' relation
    5. Waits for active/idle
    6. Yields control
    7. Removes both istio apps and waits for active/idle
    """
    # Deploy istio pilot (control plane)
    juju.deploy(
        "istio-k8s",
        app=ISTIO_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )

    # Deploy istio ingress gateway
    juju.deploy(
        "istio-ingress-k8s",
        app=ISTIO_INGRESS_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )

    juju.wait(
        all_active,
        timeout=1000,
        delay=5,
        successes=5,
    )

    # Integrate with tempo
    juju.integrate(
        f"{ISTIO_INGRESS_APP}:istio-ingress-route", f"{tempo_app_name}:istio-ingress"
    )
    juju.wait(
        all_active,
        timeout=2000,
        delay=5,
        successes=5,
    )

    yield

    # Cleanup
    juju.remove_application(ISTIO_INGRESS_APP, force=True, destroy_storage=True)
    juju.remove_application(ISTIO_APP, force=True, destroy_storage=True)
    juju.wait(
        all_active,
        timeout=1000,
        delay=5,
        successes=5,
    )


def get_ingress_host(juju: Juju, ingress_type: str) -> str:
    """Get the ingress hostname/IP based on ingress type.

    Args:
        juju: Juju instance
        ingress_type: Either "traefik" or "istio"

    Returns:
        The hostname or IP address of the ingress
    """
    if ingress_type == "traefik":
        return get_ingress_proxied_hostname(juju)
    elif ingress_type == "istio":
        return get_istio_ingress_ip(juju, ISTIO_INGRESS_APP)
    else:
        raise ValueError(f"Unknown ingress type: {ingress_type}")


@pytest.fixture(scope="module")
def ingress_setup(request, juju):
    """Deploy ingress based on the parametrized ingress_type.

    This fixture is parametrized indirectly with a module scope
    meaning it will deploy the ingress once and keep it up for all
    tests using this fixture with the same parameter value.
    """
    ingress_type = request.param
    logger.info(f"Setting up {ingress_type} ingress for module scope.")

    if ingress_type == "traefik":
        with traefik_ingress(juju, TEMPO_APP):
            yield ingress_type
    elif ingress_type == "istio":
        with istio_ingress(juju, TEMPO_APP):
            yield ingress_type
    else:
        raise ValueError(f"Unknown ingress type: {ingress_type}")

    logger.info(f"Tearing down {ingress_type} ingress.")


@pytest.mark.setup
def test_build_and_deploy(juju: Juju, tempo_charm: Path):
    # GIVEN an empty model
    # WHEN deploying the tempo cluster
    deploy_monolithic_cluster(juju)

    # THEN the s3-integrator, coordinator, and worker are all in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP),
        timeout=2000,
    )


@pytest.mark.setup
def test_force_enable_protocols(juju: Juju):
    # GIVEN a tempo cluster with only otlp_http protocol enabled
    # WHEN we force enable all other tracing protocols
    config = {
        f"always_enable_{protocol}": "True"
        for protocol in list(protocols_endpoints.keys())
    }
    juju.config(TEMPO_APP, config)
    # THEN both the tempo coordinator and worker are in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP),
        timeout=2000,
        # wait for an idle period
        delay=5,
        successes=3,
    )


@pytest.mark.parametrize("ingress_setup", ["traefik", "istio"], indirect=True)
@pytest.mark.parametrize("protocol", protocols_endpoints.keys())
def test_verify_traces_force_enabled_protocols(
    juju: Juju, nonce, ingress_setup, protocol
):
    # GIVEN a Tempo cluster with all tracing protocols enabled and ingress deployed
    ingress_type = ingress_setup
    tempo_host = get_ingress_host(juju, ingress_type)
    tempo_endpoint = get_tempo_ingressed_endpoint(
        tempo_host,
        protocol=protocol,
        tls=False,
    )

    # WHEN we emit a trace in any of the supported tracing protocols using the ingress url
    logger.info(
        f"emitting & verifying trace using {protocol} protocol via {ingress_type} ingress."
    )
    service_name = f"tracegen-{protocol}-{ingress_type}"
    emit_trace(
        tempo_endpoint,
        juju,
        nonce=nonce,
        verbose=1,
        proto=protocol,
        service_name=service_name,
    )

    # THEN using the ingress url, we can verify that the trace has been ingested
    query_traces_patiently_from_client_localhost(
        tempo_host, service_name=service_name, nonce=nonce, tls=False
    )


@pytest.mark.parametrize("ingress_setup", ["traefik", "istio"], indirect=True)
def test_workload_traces(juju: Juju, ingress_setup):
    # GIVEN a tempo cluster with ingress deployed
    ingress_type = ingress_setup
    tempo_host = get_ingress_host(juju, ingress_type)

    # THEN using the ingress url, we can verify that traces from the tempo workload are ingested
    logger.info(f"verifying workload traces via {ingress_type} ingress.")
    assert query_traces_patiently_from_client_localhost(
        tempo_host,
        service_name="tempo-scalable-single-binary",
        tls=False,
    )


@pytest.mark.teardown
def test_teardown(juju: Juju):
    # GIVEN a model with the tempo cluster
    # WHEN we remove the coordinator and the worker
    juju.remove_application(TEMPO_APP)
    juju.remove_application(WORKER_APP)

    # THEN nothing throws an exception
