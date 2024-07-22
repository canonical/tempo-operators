#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from helpers import deploy_cluster, get_application_ip, get_traces_patiently
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
APP_REMOTE_NAME = "tempo-source"


@pytest.mark.abort_on_fail
async def test_build_and_deploy(ops_test: OpsTest):
    tempo_charm = "/home/michael/Work/tempo-coordinator-k8s-operator/charm"
    resources = {
        "nginx-image": METADATA["resources"]["nginx-image"]["upstream-source"],
        "nginx-prometheus-exporter-image": METADATA["resources"][
            "nginx-prometheus-exporter-image"
        ]["upstream-source"],
    }

    await asyncio.gather(
        ops_test.model.deploy(tempo_charm, resources=resources, application_name=APP_REMOTE_NAME),
        ops_test.model.deploy(tempo_charm, resources=resources, application_name=APP_NAME),
    )

    # deploy cluster
    await deploy_cluster(ops_test, APP_NAME)

    await asyncio.gather(
        ops_test.model.wait_for_idle(status="active", raise_on_blocked=True, timeout=1000)
    )


@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    await ops_test.model.integrate(APP_NAME + ":tracing", APP_REMOTE_NAME + ":self-tracing")
    await ops_test.model.wait_for_idle(
        status="active",
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_verify_trace_http(ops_test: OpsTest):
    # adjust update-status interval to generate a charm tracing span faster
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    # Verify traces from `tempo-source` are ingested into remote tempo instance
    assert await get_traces_patiently(
        await get_application_ip(ops_test, APP_NAME),
        service_name="tempo-source-charm",
        tls=False,
    )

    # adjust back to the default interval time
    await ops_test.model.set_config({"update-status-hook-interval": "5m"})
