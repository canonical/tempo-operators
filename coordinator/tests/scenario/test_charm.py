from pathlib import Path

import pytest
from ops.testing import State, Model
from unittest.mock import patch

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(params=(True, False))
def base_state(request, s3):
    return State(leader=request.param, relations=[s3])


def test_smoke(context, base_state):
    # verify the charm runs at all with and without leadership
    context.run(context.on.start(), base_state)


@pytest.mark.parametrize(("hostname", "expected_service_hostname"),
                                    (("tempo-0.tempo-headless.test.svc.cluster.local", "tempo-coordinator-k8s.test.svc.cluster.local"),
                                    ("tempo-0.tempo-headless.test.svc.custom.domain","tempo-coordinator-k8s.test.svc.custom.domain"),
                                    ("tempo-0.tempo-headless.test.svc.custom.svc.domain","tempo-coordinator-k8s.test.svc.custom.svc.domain"),
                                    ("localhost", "localhost"),
                                    ("my.custom.domain", "my.custom.domain"),
                                    ("192.0.2.1", "192.0.2.1")))
def test_service_hostname(context, hostname, expected_service_hostname):
    # GIVEN a hostname
    state = State(model=Model(name="test"))
    # WHEN any event fires
    with patch("charm.TempoCoordinatorCharm.hostname", hostname):
        with context(context.on.update_status(), state) as mgr:
            charm = mgr.charm
            # THEN if hostname is a valid k8s pod fqdn, service_hostname is set to the k8s service fqdn
            # else service_hostname is set to whatever value hostname has 
            assert expected_service_hostname == charm.service_hostname
