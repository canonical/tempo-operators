from unittest.mock import MagicMock, patch

import pytest
from scenario import Container, Context, Relation

from charm import TempoCoordinatorCharm


@pytest.fixture()
def coordinator():
    return MagicMock()


@pytest.fixture
def tempo_charm():
    with patch("lightkube.core.client.GenericSyncClient"):
        with patch("charm.TempoCoordinatorCharm.are_certificates_on_disk", False):
            yield TempoCoordinatorCharm


@pytest.fixture(scope="function")
def context(tempo_charm):
    return Context(charm_type=tempo_charm)


@pytest.fixture(scope="function")
def s3_config():
    return {
        "access-key": "key",
        "bucket": "tempo",
        "endpoint": "http://1.2.3.4:9000",
        "secret-key": "soverysecret",
    }


@pytest.fixture(scope="function")
def s3(s3_config):
    return Relation(
        "s3",
        remote_app_data=s3_config,
        local_unit_data={"bucket": "tempo"},
    )


@pytest.fixture(scope="function")
def all_worker():
    return Relation(
        "tempo-cluster",
        remote_app_data={"role": '"all"'},
    )


@pytest.fixture(scope="function")
def nginx_container():
    return Container(
        "nginx",
        can_connect=True,
    )


@pytest.fixture(scope="function")
def nginx_prometheus_exporter_container():
    return Container(
        "nginx-prometheus-exporter",
        can_connect=True,
    )
