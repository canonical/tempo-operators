from unittest.mock import MagicMock
import ops
from charms.tempo_coordinator_k8s.v0.tracing import charm_tracing_config


def test_charm_tracing_config_if_get_endpoint_raises():
    mm= MagicMock()
    mm.is_ready.return_value=True
    mm.get_endpoint.side_effect = ops.ModelError('ERROR permission denied\n')
    assert charm_tracing_config(mm, None) == (None,None)