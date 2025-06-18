#!/usr/bin/env python3
# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.

import logging
from pathlib import Path

import jubilant
import pytest
from jubilant import Juju

from helpers import (
    PYROSCOPE_APP,
    WORKER_APP,
    deploy_monolithic_cluster,
    TRAEFIK_APP,
)

logger = logging.getLogger(__name__)


@pytest.mark.setup
def test_build_and_deploy(juju: Juju):
    # GIVEN an empty model
    # WHEN deploying the tempo cluster and traefik
    juju.deploy("traefik-k8s", app=TRAEFIK_APP, channel="edge", trust=True)
    deploy_monolithic_cluster(juju)

    # THEN the s3-integrator, coordinator, worker, and traefik are all in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, TRAEFIK_APP, PYROSCOPE_APP, WORKER_APP),
        error=jubilant.any_error,
        timeout=2000,
    )


def test_relate_ingress(juju: Juju):
    # GIVEN a model with a tempo cluster and traefik
    # WHEN we integrate the tempo cluster with traefik over ingress
    juju.integrate(PYROSCOPE_APP + ":ingress", TRAEFIK_APP + ":ingress")

    # THEN the coordinator, worker, and traefik are all in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, TRAEFIK_APP, PYROSCOPE_APP, WORKER_APP),
        error=jubilant.any_error,
        timeout=2000,
    )




@pytest.mark.teardown
def test_remove_ingress(juju: Juju):
    # GIVEN a model with traefik and the tempo cluster integrated
    # WHEN we remove the ingress relation
    juju.remove_relation(PYROSCOPE_APP + ":ingress", TRAEFIK_APP + ":ingress")

    # THEN the coordinator and worker are in active/idle state
    juju.wait(
        lambda status: jubilant.all_active(status, PYROSCOPE_APP, WORKER_APP),
        error=jubilant.any_error,
        timeout=2000,
    )


@pytest.mark.teardown
def test_teardown(juju: Juju):
    # GIVEN a model with traefik and the tempo cluster
    # WHEN we remove traefik, the coordinator, and the worker
    juju.remove_application(PYROSCOPE_APP)
    juju.remove_application(WORKER_APP)
    juju.remove_application(TRAEFIK_APP)

    # THEN nothing throws an exception
