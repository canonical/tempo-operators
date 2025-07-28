import pytest
from jubilant import Juju

from tests.integration.conftest import deployment_factory
from assertions import assert_charm_traces_ingested


@pytest.fixture
def deployment(juju, do_setup, do_teardown):
    # set up a distributed deployment with tls and no ingress
    with deployment_factory(
        tls=True,
        distributed=True,
        juju=juju,
        do_setup=do_setup,
        do_teardown=do_teardown,
    ) as juju:
        yield juju


def test_charm_tracing(deployment: Juju, distributed, tls):
    assert_charm_traces_ingested(
        deployment=deployment, distributed=distributed, tls=tls
    )
