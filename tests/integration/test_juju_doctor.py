import pytest
import sh
from jubilant import Juju, all_active, all_blocked

from tests.integration.helpers import S3_APP, TEMPO_APP, WORKER_APP, deploy_s3


def _deploy_worker(juju: Juju, worker_charm, role: str, scale: int):
    """Deploy a worker for a specific role."""
    charm_url, channel, resources = worker_charm
    juju.deploy(
        charm_url,
        role,
        channel=channel,
        resources=resources,
        trust=True,
        config={
            "role-all": False,
            f"role-{role}": True,
        },
        num_units=scale,
    )


@pytest.mark.juju_setup
def test_deploy_workers(juju: Juju, worker_charm):
    # GIVEN an empty model

    # WHEN deploying the worker
    _deploy_worker(juju, worker_charm, "querier", 1)
    _deploy_worker(juju, worker_charm, "query-frontend", 1)
    _deploy_worker(juju, worker_charm, "ingester", 3)
    _deploy_worker(juju, worker_charm, "distributor", 1)
    _deploy_worker(juju, worker_charm, "compactor", 1)
    _deploy_worker(juju, worker_charm, "metrics-generator", 1)

    # THEN worker will be blocked because of missing coordinator integration
    juju.wait(
        lambda status: all_blocked(
            status,
            "querier",
            "query-frontend",
            "ingester",
            "distributor",
            "compactor",
            "metrics-generator",
        ),
        timeout=1000,
    )


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
    juju.integrate(TEMPO_APP, "querier")
    juju.integrate(TEMPO_APP, "query-frontend")
    juju.integrate(TEMPO_APP, "ingester")
    juju.integrate(TEMPO_APP, "distributor")
    juju.integrate(TEMPO_APP, "compactor")
    juju.integrate(TEMPO_APP, "metrics-generator")

    # THEN both the coordinator and the worker become active
    juju.wait(
        lambda status: all_active(
            status,
            TEMPO_APP,
            "querier",
            "query-frontend",
            "ingester",
            "distributor",
            "compactor",
        ),
        timeout=5000,
    )


def test_juju_doctor_probes(juju: Juju):
    # GIVEN the full model
    # THEN juju-doctor passes
    try:
        sh.uvx("juju-doctor", "check", probe="file://../probes/cluster-consistency.yaml", model=juju.model)
    except sh.ErrorReturnCode as e:
        pytest.fail(f"juju-doctor failed:\n{e.stderr.decode()}")
