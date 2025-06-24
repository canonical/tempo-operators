#!/usr/bin/env python3
# Copyright 2024 Ubuntu
# See LICENSE file for licensing details.

import json
import logging
from pathlib import Path

import jubilant
import pytest
import yaml
from jubilant import Juju
from tenacity import retry, stop_after_attempt, wait_fixed

from coordinator.tests.integration.helpers import deploy_prometheus
from helpers import run_command, TEMPO_APP
from tests.integration.helpers import TEMPO_RESOURCES, PROMETHEUS_APP

logger = logging.getLogger(__name__)

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())

# retry up to 20 times, waiting 5 seconds between attempts
@retry(stop=stop_after_attempt(20), wait=wait_fixed(5))
def wait_for_ready_prometheus(juju: Juju):
    cmd = ["curl", "-sS", "http://localhost:9090/-/ready"]
    result = run_command(juju.model, PROMETHEUS_APP, 0, command=cmd)
    assert "Prometheus Server is Ready." in result.decode("utf-8")


@pytest.mark.setup
def test_deploy(juju: Juju, tempo_charm: Path):
    """Build the charm-under-test and deploy it together with related charms."""
    # Deploy the charms and wait for active/idle status
    juju.deploy(tempo_charm, TEMPO_APP, trust=True, resources=TEMPO_RESOURCES)
    deploy_prometheus(juju)
    juju.integrate(f"{PROMETHEUS_APP}:metrics-endpoint", f"{TEMPO_APP}:metrics-endpoint")

    juju.wait(
        lambda status: jubilant.all_active(status, PROMETHEUS_APP) and
                       jubilant.all_blocked(status, TEMPO_APP),
        timeout=600,
    )
    wait_for_ready_prometheus(juju)

def test_scrape_jobs(juju: Juju):
    # Check scrape jobs
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/targets"]
    result = run_command(juju.model, PROMETHEUS_APP, 0, command=cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))

    active_targets = result_json["data"]["activeTargets"]

    for at in active_targets:
        assert at["labels"]["juju_application"] in (TEMPO_APP, PROMETHEUS_APP)


def test_rules(juju: Juju):
    # Check Rules
    cmd = ["curl", "-sS", "http://localhost:9090/api/v1/rules"]
    result = run_command(juju.model, PROMETHEUS_APP, 0, command=cmd)
    logger.info(result)
    result_json = json.loads(result.decode("utf-8"))
    groups = result_json["data"]["groups"]

    for group in groups:
        for rule in group["rules"]:
            assert rule["labels"]["juju_application"] in (TEMPO_APP, PROMETHEUS_APP)
