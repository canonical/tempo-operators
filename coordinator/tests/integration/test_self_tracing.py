#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

from pathlib import Path

import pytest
from jubilant import Juju
from tenacity import retry, stop_after_attempt, wait_fixed

from helpers import (
    WORKER_APP,
    deploy_monolithic_cluster,
    get_app_ip_address,
    TEMPO_APP,
    get_ingested_traces_service_names
)

APP_REMOTE_NAME = "tempo-remote"
APP_REMOTE_WORKER_NAME = "tempo-remote-worker"
APP_REMOTE_S3 = "tempo-remote-s3"


TEMPO_WORKER_CHARM_TRACES_SVC_NAME = "tempo-worker-charm"
# FIXME: replace with WORKER_APP in the next PR
#  (as the change to the worker charm will be released)


@pytest.mark.setup
def test_build_and_deploy(juju: Juju):
    # deploy cluster
    deploy_monolithic_cluster(juju)


@retry(stop=stop_after_attempt(10), wait=wait_fixed(10))
def test_verify_self_traces_collected(juju: Juju):
    # adjust update-status interval to generate a charm tracing span faster
    juju.cli("model-config", "update-status-hook-interval=5s")

    # Verify that tempo is ingesting traces from the following services:
    services = get_ingested_traces_service_names(get_app_ip_address(juju, TEMPO_APP), tls=False)

    for svc in (
        TEMPO_APP, # coordinator charm traces
        TEMPO_WORKER_CHARM_TRACES_SVC_NAME,  # worker charm traces
        "tempo-scalable-single-binary", # worker's workload traces
    ):
        assert svc in services

    # adjust back to the default interval time
    juju.cli("model-config", "update-status-hook-interval=5m")


@pytest.mark.setup
def test_deploy_second_tempo(juju: Juju, tempo_charm: Path):
    # deploy a second tempo stack
    deploy_monolithic_cluster(juju, worker=APP_REMOTE_WORKER_NAME, s3=APP_REMOTE_S3, coordinator=APP_REMOTE_NAME)

    juju.integrate(TEMPO_APP + ":self-charm-tracing", APP_REMOTE_NAME + ":tracing")
    juju.wait(
        lambda status: all(status.apps[app].is_active for app in [TEMPO_APP, WORKER_APP, APP_REMOTE_NAME, APP_REMOTE_WORKER_NAME]),
        timeout=1000
    )


@retry(stop=stop_after_attempt(10), wait=wait_fixed(10))
def test_verify_self_traces_sent_to_remote(juju: Juju):
    # adjust update-status interval to generate a charm tracing span faster
    juju.cli("model-config", "update-status-hook-interval=5s")

    # Verify traces from `this tempo` are sent to remote instance
    services = get_ingested_traces_service_names(get_app_ip_address(juju, APP_REMOTE_NAME), tls=False)
    for svc in (
        TEMPO_APP, # coordinator charm traces
        TEMPO_WORKER_CHARM_TRACES_SVC_NAME,  # worker charm traces
        APP_REMOTE_NAME, # remote coordinator charm traces
        APP_REMOTE_WORKER_NAME,  # remote worker charm traces
    ):
        assert svc in services

    # adjust back to the default interval time
    juju.cli("model-config", "update-status-hook-interval=5m")