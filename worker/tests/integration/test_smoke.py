
import logging
import pytest
from jubilant import Juju, all_blocked
from pathlib import Path
from helpers import TEMPO_WORKER_APP, RESOURCES

logger = logging.getLogger(__name__)

@pytest.mark.setup
def test_deploy(juju: Juju, charm: Path):
    juju.deploy(
        charm, TEMPO_WORKER_APP, resources=RESOURCES, trust=True
    )

    # coordinator will be blocked because of missing s3 and workers integration
    juju.wait(
        lambda status: all_blocked(status, TEMPO_WORKER_APP),
        timeout=1000,
        delay=5,
        successes=3,
    )


def test_scale_tempo_up_stays_blocked(juju: Juju):
    juju.cli("add-unit", TEMPO_WORKER_APP, "-n", "1")
    juju.wait(
        lambda status: all_blocked(status, TEMPO_WORKER_APP),
        timeout=1000,
        delay=5,
        successes=3,
    )


@pytest.mark.teardown
def test_teardown(juju: Juju):
    juju.remove_application(TEMPO_WORKER_APP)
