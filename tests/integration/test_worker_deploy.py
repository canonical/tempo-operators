import pytest
from jubilant import Juju, all_active, all_blocked

from tests.integration.helpers import S3_APP, TEMPO_APP, WORKER_APP, deploy_s3


@pytest.mark.setup
def test_deploy_worker(juju: Juju, worker_charm):
    # GIVEN an empty model

    # WHEN deploying the worker
    charm_url, channel, resources = worker_charm
    juju.deploy(
        charm_url,
        WORKER_APP,
        channel=channel,
        resources=resources,
        trust=True,
    )

    # THEN worker will be blocked because of missing coordinator integration
    juju.wait(lambda status: all_blocked(status, WORKER_APP), timeout=1000)


def test_all_active_when_coordinator_and_s3_added(juju: Juju, coordinator_charm):
    # GIVEN a model with a worker

    # WHEN deploying and integrating the minimal tempo cluster
    deploy_s3(juju)
    charm_url, channel, resources = coordinator_charm
    juju.deploy(
        charm_url,
        TEMPO_APP,
        channel=channel,
        resources=resources,
        trust=True,
    )
    juju.integrate(TEMPO_APP, S3_APP)
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
