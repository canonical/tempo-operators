from unittest.mock import MagicMock, patch

import pytest as pytest
from cosl.coordinated_workers.coordinator import Coordinator

from tempo_config import (
    MINIMAL_DEPLOYMENT,
    RECOMMENDED_DEPLOYMENT,
    TEMPO_ROLES_CONFIG,
    TempoRole,
)


@patch("cosl.coordinated_workers.coordinator.Coordinator.__init__", return_value=None)
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
def test_coherent(mock_coordinator, roles, expected):

    mc = Coordinator(None, None, "", "", 0, None, None, None)
    cluster_mock = MagicMock()
    cluster_mock.gather_roles = MagicMock(return_value=roles)
    mc.cluster = cluster_mock
    mc._is_coherent = None
    mc.roles_config = TEMPO_ROLES_CONFIG

    assert mc.is_coherent is expected


@patch("cosl.coordinated_workers.coordinator.Coordinator.__init__", return_value=None)
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
def test_recommended(mock_coordinator, roles, expected):
    mc = Coordinator(None, None, "", "", 0, None, None, None)
    cluster_mock = MagicMock()
    cluster_mock.gather_roles = MagicMock(return_value=roles)
    mc.cluster = cluster_mock
    mc._is_recommended = None
    mc.roles_config = TEMPO_ROLES_CONFIG

    assert mc.is_recommended is expected
