from unittest.mock import MagicMock

import pytest as pytest

from coordinator import (
    MINIMAL_DEPLOYMENT,
    RECOMMENDED_DEPLOYMENT,
    TempoCoordinator,
    TempoRole,
)


def _to_endpoint_name(role: TempoRole):
    return role.value.replace("_", "-")


ALL_TEMPO_RELATION_NAMES = list(map(_to_endpoint_name, TempoRole))


@pytest.mark.parametrize(
    "roles, expected",
    (
        ({TempoRole.querier: 1}, False),
        ({TempoRole.distributor: 1}, False),
        ({TempoRole.distributor: 1, TempoRole.ingester: 1}, False),
        (MINIMAL_DEPLOYMENT, True),
        (RECOMMENDED_DEPLOYMENT, True),
    ),
)
def test_coherent(roles, expected):
    mock = MagicMock()
    mock.gather_roles = MagicMock(return_value=roles)
    mc = TempoCoordinator(mock)
    assert mc.is_coherent is expected


@pytest.mark.parametrize(
    "roles, expected",
    (
        ({TempoRole.query_frontend: 1}, False),
        ({TempoRole.distributor: 1}, False),
        ({TempoRole.distributor: 1, TempoRole.ingester: 1}, False),
        (MINIMAL_DEPLOYMENT, False),
        (RECOMMENDED_DEPLOYMENT, True),
    ),
)
def test_recommended(roles, expected):
    mock = MagicMock()
    mock.gather_roles = MagicMock(return_value=roles)
    mc = TempoCoordinator(mock)
    assert mc.is_recommended is expected
