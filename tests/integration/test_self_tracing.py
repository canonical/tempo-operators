#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

from pathlib import Path

import jubilant
from jubilant import Juju

from helpers import (
    WORKER_APP,
    deploy_monolithic_cluster,
    get_app_ip_address,
    get_traces_patiently, TEMPO_APP,
)
from tests.integration.helpers import TEMPO_RESOURCES

APP_REMOTE_NAME = "tempo-remote"


def test_build_and_deploy(juju: Juju, tempo_charm: Path):
    # deploy cluster
    deploy_monolithic_cluster(juju)

    # deploy another tempo instance, which will go to blocked as it misses workers and s3
    juju.deploy(
        tempo_charm, resources=TEMPO_RESOURCES, app=APP_REMOTE_NAME, trust=True
    )
    juju.wait(
        lambda status: jubilant.all_blocked(status, APP_REMOTE_NAME),
        timeout=2000
    )


def test_verify_trace_http_self(juju: Juju):
    # adjust update-status interval to generate a charm tracing span faster
    juju.cli("model-config", "update-status-hook-interval=5s")

    # Verify traces from `tempo` are ingested into self Tempo
    assert get_traces_patiently(
        get_app_ip_address(juju, TEMPO_APP),
        service_name=f"{TEMPO_APP}-charm",
        tls=False,
    )

    # adjust back to the default interval time
    juju.cli("model-config", "update-status-hook-interval=5m")


def test_relate_remote_instance(juju: Juju):
    juju.integrate(TEMPO_APP + ":tracing", APP_REMOTE_NAME + ":self-charm-tracing")
    juju.wait(
        lambda status: all(status.apps[app].is_active for app in [TEMPO_APP, WORKER_APP]),
        timeout=1000
    )


def test_verify_trace_http_remote(juju: Juju):
    # adjust update-status interval to generate a charm tracing span faster
    juju.cli("model-config", "update-status-hook-interval=5s")

    # Verify traces from `tempo-remote` are ingested into tempo instance
    assert get_traces_patiently(
        get_app_ip_address(juju, TEMPO_APP),
        service_name=f"{APP_REMOTE_NAME}-charm",
        tls=False,
    )

    # adjust back to the default interval time
    juju.cli("model-config", "update-status-hook-interval=5m")


def test_workload_traces(juju: Juju):
    # verify traces from tempo-scalable-single-binary are ingested
    assert get_traces_patiently(
        get_app_ip_address(juju, TEMPO_APP),
        service_name="tempo-scalable-single-binary",
        tls=False,
    )
