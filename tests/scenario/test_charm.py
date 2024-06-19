from pathlib import Path

import pytest
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from scenario import State
from scenario.sequences import check_builtin_sequences

TEMPO_CHARM_ROOT = Path(__file__).parent.parent.parent


@pytest.fixture(params=(True, False))
def base_state(request, s3):
    return State(leader=request.param, relations=[s3])


def test_builtin_sequences(tempo_charm, base_state):
    with charm_tracing_disabled():
        check_builtin_sequences(tempo_charm, template_state=base_state)


def test_smoke(context, base_state):
    # verify the charm runs at all with and without leadership
    with charm_tracing_disabled():
        context.run("start", base_state)
