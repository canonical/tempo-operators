#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import pytest
from jubilant import Juju
from tenacity import retry, stop_after_attempt, wait_fixed

from tests.integration.helpers import (
    WORKER_APP,
    deploy_monolithic_cluster,
    get_app_ip_address,
    TEMPO_APP,
    get_ingested_traces_service_names,
)


APP_REMOTE_NAME = "tempo-remote"
APP_REMOTE_WORKER_NAME = "tempo-remote-worker"
APP_REMOTE_S3 = "tempo-remote-s3"


@pytest.mark.setup
def test_deploy_second_tempo(juju: Juju):
    # deploy a second tempo stack, in monolithic mode
    deploy_monolithic_cluster(
        juju,
        worker=APP_REMOTE_WORKER_NAME,
        s3=APP_REMOTE_S3,
        coordinator=APP_REMOTE_NAME,
    )

    # send the charm and workload traces from `this stack` to the remote one
    juju.integrate(TEMPO_APP + ":self-charm-tracing", APP_REMOTE_NAME + ":tracing")
    juju.integrate(TEMPO_APP + ":self-workload-tracing", APP_REMOTE_NAME + ":tracing")
    juju.wait(
        lambda status: all(
            status.apps[app].is_active
            for app in [TEMPO_APP, WORKER_APP, APP_REMOTE_NAME, APP_REMOTE_WORKER_NAME]
        ),
        timeout=1000,
    )


@retry(stop=stop_after_attempt(10), wait=wait_fixed(10))
def test_verify_self_charm_traces_sent_to_remote(juju: Juju):
    # adjust update-status interval to generate a charm tracing span faster
    juju.cli("model-config", "update-status-hook-interval=5s")

    # Verify traces from `this tempo` are sent to remote instance
    services = get_ingested_traces_service_names(
        get_app_ip_address(juju, APP_REMOTE_NAME), tls=False
    )
    for svc in (
        TEMPO_APP,  # 'local' coordinator charm traces
        WORKER_APP,  # 'local' worker charm traces
        APP_REMOTE_NAME,  # remote coordinator charm traces
        APP_REMOTE_WORKER_NAME,  # remote worker charm traces
    ):
        assert svc in services

    # adjust back to the default interval time
    juju.cli("model-config", "update-status-hook-interval=5m")


# TODO: add a test that verifies that workload traces are also being sent to the remote cluster;
#  to do that, we'll probably need to update the query in helpers.get_traces to also filter the
#  results on `juju_application=<TEMPO_APP>`
