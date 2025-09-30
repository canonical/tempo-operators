from pathlib import Path

import jubilant
import pytest
from jubilant import Juju, all_blocked

from helpers import deploy_monolithic_cluster, TEMPO_APP, S3_APP
from tests.integration.helpers import deploy_tempo


@pytest.mark.setup
def test_deploy_tempo(juju: Juju, tempo_charm: Path):
    deploy_tempo(juju, tempo_charm)

    # coordinator will be blocked because of missing s3 and workers integration
    juju.wait(lambda status: all_blocked(status, TEMPO_APP), timeout=1000)


def test_scale_tempo_up_stays_blocked(juju: Juju):
    juju.cli("add-unit", TEMPO_APP, "-n", "1")
    juju.wait(lambda status: all_blocked(status, TEMPO_APP), timeout=1000)


@pytest.mark.setup
def test_tempo_active_when_deploy_s3_and_workers(juju: Juju):
    deploy_monolithic_cluster(juju)


@pytest.mark.teardown
def test_tempo_blocks_if_s3_goes_away(juju: Juju):
    juju.remove_relation(S3_APP, TEMPO_APP)
    # FIXME: s3 stubbornly refuses to die
    # juju.remove_application(S3_APP, force=True)
    juju.wait(lambda status: jubilant.all_blocked(status, TEMPO_APP), timeout=1000)
