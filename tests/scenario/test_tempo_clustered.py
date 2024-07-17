import datetime
from unittest.mock import MagicMock, patch

import pytest
import scenario
from charms.tempo_k8s.v2.tracing import TracingRequirerAppData
from charms.tls_certificates_interface.v3.tls_certificates import ProviderCertificate
from scenario import Relation, State
from tempo_cluster import TempoClusterProviderAppData

from charm import TempoCoordinatorCharm
from tempo import Tempo
from tests.scenario.helpers import get_tempo_config


@pytest.fixture
def all_worker_with_initial_config(all_worker: Relation, s3_config):
    container = MagicMock()
    container.can_connect = lambda: True
    # prevent tls_ready from reporting True
    container.exists = lambda path: (
        False if path in [Tempo.tls_cert_path, Tempo.tls_key_path, Tempo.tls_ca_path] else True
    )
    initial_config = Tempo(container).generate_config(
        ["otlp_http"], s3_config, {"all": "localhost"}
    )
    new_local_app_data = TempoClusterProviderAppData(
        tempo_config=initial_config,
        loki_endpoints={},
        ca_cert="foo cert",
        server_cert="bar cert",
        privkey_secret_id="super secret",
        tempo_receiver={"otlp_http": "https://foo.com/fake_receiver"},
    ).dump()
    return all_worker.replace(local_app_data=new_local_app_data)


@pytest.fixture
def certs_relation():
    return scenario.Relation("certificates")


MOCK_SERVER_CERT = "SERVER_CERT-foo"
MOCK_CA_CERT = "CA_CERT-foo"


@pytest.fixture(autouse=True)
def patch_certs():
    cert = ProviderCertificate(
        relation_id=42,
        application_name="tempo",
        csr="CSR",
        certificate=MOCK_SERVER_CERT,
        ca=MOCK_CA_CERT,
        chain=[],
        revoked=False,
        expiry_time=datetime.datetime(2050, 4, 1),
    )
    with patch(
        "charms.observability_libs.v1.cert_handler.CertHandler.get_cert", new=lambda _: cert
    ):
        yield


@pytest.fixture
def state_with_certs(
    context, s3, certs_relation, nginx_container, nginx_prometheus_exporter_container
):
    return context.run(
        certs_relation.joined_event,
        scenario.State(
            leader=True,
            relations=[s3, certs_relation],
            containers=[nginx_container, nginx_prometheus_exporter_container],
        ),
    )


def test_certs_ready(context, state_with_certs):
    with context.manager("update-status", state_with_certs) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        assert charm.cert_handler.server_cert == MOCK_SERVER_CERT
        assert charm.cert_handler.ca_cert == MOCK_CA_CERT
        assert charm.cert_handler.private_key


def test_cluster_relation(context, state_with_certs, all_worker):
    clustered_state = state_with_certs.replace(relations=state_with_certs.relations + [all_worker])
    state_out = context.run(all_worker.joined_event, clustered_state)
    cluster_out = state_out.get_relations(all_worker.endpoint)[0]
    local_app_data = TempoClusterProviderAppData.load(cluster_out.local_app_data)

    assert local_app_data.ca_cert == MOCK_CA_CERT
    assert local_app_data.server_cert == MOCK_SERVER_CERT
    secret = [s for s in state_out.secrets if s.id == local_app_data.privkey_secret_id][0]

    # certhandler's vault uses revision 0 to store an uninitialized-vault marker
    assert secret.contents[1]["private-key"]

    assert local_app_data.tempo_config


@pytest.mark.parametrize("requested_protocol", ("otlp_grpc", "zipkin"))
def test_tempo_restart_on_ingress_v2_changed(
    context,
    tmp_path,
    requested_protocol,
    s3,
    s3_config,
    all_worker_with_initial_config,
    nginx_container,
    nginx_prometheus_exporter_container,
):
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
    state_out = context.run(tracing.changed_event, state)

    # THEN
    # Tempo pushes a new config to the all_worker
    new_config = get_tempo_config(state_out)
    expected_config = Tempo().generate_config(
        ["otlp_http", requested_protocol], s3_config, {"all": "localhost"}
    )
    assert new_config == expected_config
