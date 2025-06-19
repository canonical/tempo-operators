from jubilant import Juju

from tests.integration.conftest import TEMPO_APP


def test_scale_up_coordinator(deployment: Juju):
    deployment.cli("add-unit", "-n", "2", TEMPO_APP)