# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.
import logging

from pytest import fixture
from helpers import get_charm

logger = logging.getLogger(__name__)

@fixture(scope="session")
def charm():
    """Charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    You can also set `CHARM_PATH` env variable to use an already existing built charm.
    """
    return get_charm()
