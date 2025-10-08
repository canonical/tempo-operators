import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
import scenario
from charms.tempo_coordinator_k8s.v0.tracing import TracingRequirerAppData
from coordinated_workers.interfaces.cluster import (
    ClusterProvider,
    ClusterProviderAppData,
)
from coordinated_workers.coordinator import TLSConfig
from scenario import Relation, State

from charm import TempoCoordinatorCharm
from tempo import Tempo
from tests.unit.test_charm.helpers import get_tempo_config


@pytest.fixture(scope="function")
def coordinator_with_initial_config():
    new_coordinator_mock = MagicMock()
    new_coordinator_mock.return_value.hostname = "tempo-test-0.test.cluster.svc.local"
    new_coordinator_mock.return_value._s3_config = {
        "access_key_id": "key",
        "bucket_name": "tempo",
        "endpoint": "1.2.3.4:9000",
        "insecure": True,
        "secret_access_key": "soverysecret",
    }
    new_coordinator_mock.return_value.cluster.gather_addresses.return_value = (
        "localhost",
    )
    new_coordinator_mock.return_value.cluster.gather_addresses_by_role.return_value = {
        "query-frontend": {"localhost"},
        "distributor": {"localhost"},
    }

    return new_coordinator_mock


@pytest.fixture
def all_worker_with_initial_config(
    all_worker: Relation, coordinator_with_initial_config
):
    initial_config = Tempo(720, lambda: []).config(
        coordinator_with_initial_config.return_value
    )

    new_local_app_data = {
        "worker_config": json.dumps(initial_config),
        "ca_cert": json.dumps("foo cert"),
        "server_cert": json.dumps("bar cert"),
        "privkey_secret_id": json.dumps("super secret"),
        "tracing_receivers": json.dumps({"otlp_http": "https://foo.com/fake_receiver"}),
    }

    return replace(all_worker, local_app_data=new_local_app_data)


@pytest.fixture
def certs_relation():
    return scenario.Relation("certificates", remote_app_data={})


MOCK_SERVER_CERT = "SERVER_CERT-foo"
MOCK_CA_CERT = "CA_CERT-foo"
MOCK_PRIVATE_KEY = "PRIVATE_KEY-foo"


@pytest.fixture(autouse=True)
def patch_certs():
    with patch(
        "coordinated_workers.coordinator.Coordinator.tls_config",
        TLSConfig(
            private_key=MOCK_PRIVATE_KEY,
            server_cert=MOCK_SERVER_CERT,
            ca_cert=MOCK_CA_CERT,
        ),
    ):
        yield


@pytest.fixture
def state_with_certs(
    context, s3, certs_relation, nginx_container, nginx_prometheus_exporter_container
):
    return context.run(
        # unlike cert_handler v1, tls_certificates v4 creates the secret on relation_created
        context.on.relation_created(certs_relation),
        scenario.State(
            leader=True,
            relations=[s3, certs_relation],
            containers=[nginx_container, nginx_prometheus_exporter_container],
        ),
    )


def test_certs_ready(context, state_with_certs):
    with context(context.on.update_status(), state_with_certs) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        assert charm.coordinator.tls_available


def test_cluster_relation(context, state_with_certs, all_worker):
    clustered_state = replace(
        state_with_certs, relations=state_with_certs.relations.union([all_worker])
    )
    state_out = context.run(context.on.relation_joined(all_worker), clustered_state)
    cluster_out = state_out.get_relations(all_worker.endpoint)[0]
    local_app_data = ClusterProviderAppData.load(cluster_out.local_app_data)

    assert local_app_data.ca_cert == MOCK_CA_CERT
    assert local_app_data.server_cert == MOCK_SERVER_CERT
    secret = [s for s in state_out.secrets if s.id == local_app_data.privkey_secret_id][
        0
    ]

    # certhandler's vault uses revision 0 to store an uninitialized-vault marker
    assert secret.latest_content["private-key"]

    assert local_app_data.worker_config


@patch.object(ClusterProvider, "gather_addresses")
@pytest.mark.parametrize("requested_protocol", ("otlp_grpc", "zipkin"))
def test_tempo_restart_on_ingress_v2_changed(
    mock_cluster,
    coordinator_with_initial_config,
    context,
    requested_protocol,
    s3,
    all_worker_with_initial_config,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    mock_cluster.return_value = ("localhost",)

    # GIVEN
    # the remote end requests an otlp_grpc endpoint

    tracing = Relation(
        "tracing",
        remote_app_data=TracingRequirerAppData(receivers=[requested_protocol]).dump(),
    )

    # WHEN
    # the charm receives a tracing(v2) relation-changed requesting an otlp_grpc receiver
    state = State(
        leader=True,
        relations=[tracing, s3, all_worker_with_initial_config],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    state_out = context.run(context.on.relation_changed(tracing), state)

    # THEN
    # Tempo pushes a new config to the all_worker
    new_config = get_tempo_config(state_out)
    expected_config = Tempo(720, lambda: []).config(
        coordinator_with_initial_config.return_value
    )
    assert new_config == expected_config
