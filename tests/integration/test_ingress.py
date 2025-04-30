#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant
import pytest
from jubilant import Juju

from helpers import (
    WORKER_APP,
    deploy_monolithic_cluster,
    emit_trace,
    get_tempo_ingressed_endpoint,
    get_traces_patiently,
    protocols_endpoints,
    TRAEFIK_APP,
    TEMPO_APP,
    get_ingress_proxied_hostname,
)


logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_build_and_deploy(juju: Juju, tempo_charm: Path):
    # GIVEN an empty model
    # WHEN deploying the tempo cluster and traefik
    juju.deploy("traefik-k8s", app=TRAEFIK_APP, channel="edge", trust=True)
    deploy_monolithic_cluster(juju)

    # THEN the s3-integrator, coordinator, worker, and traefik are all in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, TRAEFIK_APP, TEMPO_APP, WORKER_APP),
        error=jubilant.any_error,
        timeout=2000,
    )


def test_relate_ingress(juju: Juju):
    # GIVEN a model with a tempo cluster and traefik
    # WHEN we integrate the tempo cluster with traefik over ingress
    juju.integrate(TEMPO_APP + ":ingress", TRAEFIK_APP + ":traefik-route")

    # THEN the coordinator, worker, and traefik are all in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, TRAEFIK_APP, TEMPO_APP, WORKER_APP),
        error=jubilant.any_error,
        timeout=2000,
    )


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
        error=jubilant.any_error,
        timeout=2000,
    )


@pytest.mark.parametrize("protocol", protocols_endpoints.keys())
def test_verify_traces_force_enabled_protocols(juju: Juju, nonce, protocol):
    # GIVEN an ingressed Tempo cluster with all tracing protocols enabled
    tempo_host = get_ingress_proxied_hostname(juju)
    tempo_endpoint = get_tempo_ingressed_endpoint(
        tempo_host,
        protocol=protocol,
        tls=False,
    )
    # WHEN we emit a trace in any of the supported tracing protocols using the ingress url
    logger.info(f"emitting & verifying trace using {protocol} protocol.")
    service_name = f"tracegen-{protocol}"
    emit_trace(
        tempo_endpoint,
        juju,
        nonce=nonce,
        verbose=1,
        proto=protocol,
        service_name=service_name,
    )
    # THEN using the ingress url, we can verify that the trace has been ingested
    get_traces_patiently(tempo_host, service_name=service_name, nonce=nonce)


def test_workload_traces(juju: Juju):
    # GIVEN an ingressed tempo cluster
    tempo_host = get_ingress_proxied_hostname(juju)
    # WHEN the tempo cluster is in active/idle
    # THEN using the ingress url, we can verify that traces from the tempo workload are ingested
    assert get_traces_patiently(
        tempo_host,
        service_name="tempo-scalable-single-binary",
    )


@pytest.mark.teardown
def test_remove_ingress(juju: Juju):
    # GIVEN a model with traefik and the tempo cluster integrated
    # WHEN we remove the ingress relation
    juju.remove_relation(TEMPO_APP + ":ingress", TRAEFIK_APP + ":traefik-route")

    # THEN the coordinator and worker are in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP),
        error=jubilant.any_error,
        timeout=2000,
    )


@pytest.mark.teardown
def test_teardown(juju: Juju):
    # GIVEN a model with traefik and the tempo cluster
    # WHEN we remove traefik, the coordinator, and the worker
    juju.remove_application(TEMPO_APP)
    juju.remove_application(WORKER_APP)
    juju.remove_application(TRAEFIK_APP)

    # THEN nothing throws an exception
