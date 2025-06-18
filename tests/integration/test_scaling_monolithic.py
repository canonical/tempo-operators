
import jubilant
import pytest
from jubilant import Juju, all_blocked

from helpers import deploy_monolithic_cluster, PYROSCOPE_APP, S3_APP


@pytest.mark.setup
def test_deploy_pyroscope(juju: Juju, coordinator_charm):
    url, channel, resources = coordinator_charm
    juju.deploy(
        url, PYROSCOPE_APP, channel=channel, resources=resources, trust=True
    )

    # coordinator will be blocked because of missing s3 and workers integration
    juju.wait(
        lambda status: all_blocked(status, PYROSCOPE_APP),
        timeout=1000
    )


def test_scale_pyroscope_up_stays_blocked(juju: Juju):
    juju.cli("add-unit", PYROSCOPE_APP, "-n", "1")
    juju.wait(
        lambda status: all_blocked(status, PYROSCOPE_APP),
        timeout=1000
    )


@pytest.mark.setup
def test_pyroscope_active_when_deploy_s3_and_workers(juju: Juju):
    deploy_monolithic_cluster(juju, coordinator_deployed_as=PYROSCOPE_APP)


@pytest.mark.teardown
def test_pyroscope_blocks_if_s3_goes_away(juju: Juju):
    juju.remove_relation(S3_APP, PYROSCOPE_APP)
    # FIXME: s3 stubbornly refuses to die
    # juju.remove_application(S3_APP, force=True)
    juju.wait(lambda status: jubilant.all_blocked(status, PYROSCOPE_APP),
              timeout=1000)