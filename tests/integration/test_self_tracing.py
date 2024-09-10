#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import asyncio
import logging
from pathlib import Path

import pytest
import yaml
from helpers import (
    WORKER_NAME,
    deploy_cluster,
    get_application_ip,
    get_traces_patiently,
)
from pytest_operator.plugin import OpsTest

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
APP_REMOTE_NAME = "tempo-remote"


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
        ops_test.model.deploy(
            tempo_charm, resources=resources, application_name=APP_REMOTE_NAME, trust=True
        ),
    )

    # deploy cluster
    await deploy_cluster(ops_test, APP_NAME)

    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME, WORKER_NAME], status="active", raise_on_blocked=True, timeout=1000
        ),
        ops_test.model.wait_for_idle(apps=[APP_REMOTE_NAME], status="blocked", timeout=1000),
    )


@pytest.mark.abort_on_fail
async def test_verify_trace_http_self(ops_test: OpsTest):
    # adjust update-status interval to generate a charm tracing span faster
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    # Verify traces from `tempo` are ingested into self Tempo
    assert await get_traces_patiently(
        await get_application_ip(ops_test, APP_NAME),
        service_name=f"{APP_NAME}-charm",
        tls=False,
    )

    # adjust back to the default interval time
    await ops_test.model.set_config({"update-status-hook-interval": "5m"})


@pytest.mark.abort_on_fail
async def test_relate_remote_instance(ops_test: OpsTest):
    await ops_test.model.integrate(APP_NAME + ":tracing", APP_REMOTE_NAME + ":self-tracing")
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME, WORKER_NAME],
        status="active",
        timeout=1000,
    )


@pytest.mark.abort_on_fail
async def test_verify_trace_http_remote(ops_test: OpsTest):
    # adjust update-status interval to generate a charm tracing span faster
    await ops_test.model.set_config({"update-status-hook-interval": "5s"})

    # Verify traces from `tempo-remote` are ingested into tempo instance
    assert await get_traces_patiently(
        await get_application_ip(ops_test, APP_NAME),
        service_name=f"{APP_REMOTE_NAME}-charm",
        tls=False,
    )

    # adjust back to the default interval time
    await ops_test.model.set_config({"update-status-hook-interval": "5m"})
