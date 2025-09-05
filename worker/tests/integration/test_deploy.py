from pathlib import Path

import pytest
from jubilant import Juju, all_active, all_blocked

from helpers import TEMPO_APP, S3_APP, WORKER_APP
from tests.integration.helpers import WORKER_RESOURCES, deploy_minio_and_s3


@pytest.mark.setup
def test_deploy_worker(juju: Juju, tempo_worker_charm: Path):
    # GIVEN an empty model

    # WHEN deploying the worker
    juju.deploy(tempo_worker_charm, WORKER_APP, resources=WORKER_RESOURCES, trust=True)

    # THEN worker will be blocked because of missing coordinator integration
    juju.wait(lambda status: all_blocked(status, WORKER_APP), timeout=1000)


def test_all_active_when_coordinator_and_s3_added(juju: Juju):
    # GIVEN a model with a worker

    # WHEN deploying and integrating the minimal tempo cluster
    deploy_minio_and_s3(juju)
    juju.deploy("tempo-coordinator-k8s", TEMPO_APP, trust=True, channel="2/edge")
    juju.integrate(TEMPO_APP + ":s3", S3_APP + ":s3-credentials")
    juju.integrate(TEMPO_APP, WORKER_APP)

    # THEN both the coordinator and the worker become active
    juju.wait(lambda status: all_active(status, TEMPO_APP, WORKER_APP), timeout=5000)


@pytest.mark.teardown
def test_teardown(juju: Juju):
    # GIVEN the full model

    # WHEN removing the worker and the coordinator
    juju.remove_application(WORKER_APP)
    juju.remove_application(TEMPO_APP)

    # THEN nothing throws an exception
