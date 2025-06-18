# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from pytest import fixture

from tests.integration.helpers import charm_and_channel_and_resources


logger = logging.getLogger(__name__)


@fixture(scope="session")
def coordinator_charm():
    """Pyroscope coordinator used for integration testing."""
    return charm_and_channel_and_resources("coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL")

@fixture(scope="session")
def worker_charm():
    """Pyroscope worker used for integration testing."""
    return charm_and_channel_and_resources("worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL")
