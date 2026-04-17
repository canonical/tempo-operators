import jubilant
import pytest
from jubilant import Juju, all_blocked

from tests.integration.helpers import (
    S3_APP,
    TEMPO_APP,
    deploy_monolithic_cluster,
    deploy_tempo,
)


@pytest.mark.juju_setup
def test_deploy_tempo(juju: Juju):
    deploy_tempo(juju)

    # coordinator will be blocked because of missing s3 and workers integration
    juju.wait(lambda status: all_blocked(status, TEMPO_APP), timeout=1000)


def test_scale_tempo_up_stays_blocked(juju: Juju):
    juju.cli("add-unit", TEMPO_APP, "-n", "1")
    juju.wait(lambda status: all_blocked(status, TEMPO_APP), timeout=1000)


@pytest.mark.juju_setup
def test_tempo_active_when_deploy_s3_and_workers(juju: Juju):
    deploy_monolithic_cluster(juju)


@pytest.mark.juju_teardown
def test_tempo_blocks_if_s3_goes_away(juju: Juju):
    juju.remove_relation(S3_APP, TEMPO_APP)
    # FIXME: s3 stubbornly refuses to die
    # juju.remove_application(S3_APP, force=True)
    juju.wait(lambda status: jubilant.all_blocked(status, TEMPO_APP), timeout=1000)
