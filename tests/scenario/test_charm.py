from pathlib import Path

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from scenario import State

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(params=(True, False))
def base_state(request, s3):
    return State(leader=request.param, relations=[s3])


def test_smoke(context, base_state):
    # verify the charm runs at all with and without leadership
    with charm_tracing_disabled():
        context.run(context.on.start(), base_state)
