from typing import List
from unittest.mock import MagicMock, patch

import logging
import ops
import pytest
from scenario import PeerRelation, State

from nginx import Nginx
from tempo_cluster import TempoClusterProvider


logger = logging.getLogger(__name__)

@pytest.fixture
def tempo_cluster_provider():
    cluster_mock = MagicMock()
    return TempoClusterProvider(cluster_mock)


def test_nginx_config_is_list_before_crossplane(context, nginx_container, tempo_cluster_provider):
    unit = MagicMock()
    unit.get_container = nginx_container
    tempo_charm = MagicMock()
    tempo_charm.unit = MagicMock(return_value=unit)

    nginx = Nginx(tempo_charm, tempo_cluster_provider, "lolcathost")

    prepared_config = nginx._prepare_config(tls=False)
    assert isinstance(prepared_config, List)
