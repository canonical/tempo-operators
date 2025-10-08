import json
from contextlib import ExitStack
from pathlib import Path
from shutil import rmtree
from unittest.mock import MagicMock, patch

import pytest
from ops import ActiveStatus
from scenario import Container, Context, PeerRelation, Relation, Exec

from charm import PEERS_RELATION_ENDPOINT_NAME, TempoCoordinatorCharm
from contextlib import ExitStack


@pytest.fixture(autouse=True, scope="session")
def cleanup_rendered_alert_rules():
    # some tests trigger the charm to generate alert rules file in src/(loki|prometheus)_alert_rules/consolidated_rules; clean it up
    yield
    src_dir = Path(__file__).parent.parent.parent / "src"
    for alerts_dir in ("prometheus_alert_rules", "loki_alert_rules"):
        consolidated_dir = src_dir / alerts_dir / "consolidated_rules"
        if consolidated_dir.exists():
            rmtree(consolidated_dir)


@pytest.fixture()
def coordinator():
    return MagicMock()


@pytest.fixture
def tempo_charm(tmp_path):
    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "coordinated_workers.coordinator.Coordinator._consolidate_alert_rules"
            )
        )
        stack.enter_context(patch("lightkube.core.client.GenericSyncClient"))
        stack.enter_context(
            patch("charm.TempoCoordinatorCharm.are_certificates_on_disk", False)
        )
        stack.enter_context(
            patch("tempo.Tempo.tls_ca_path", str(tmp_path / "cert.tmp"))
        )
        stack.enter_context(
            patch("coordinated_workers.nginx.CA_CERT_PATH", str(tmp_path / "ca.tmp"))
        )
        stack.enter_context(
            patch.multiple(
                "coordinated_workers.coordinator.KubernetesComputeResourcesPatch",
                _namespace="test-namespace",
                _patch=lambda _: None,
                get_status=lambda _: ActiveStatus(""),
                is_ready=lambda _: True,
            )
        )
        stack.enter_context(patch("socket.getfqdn", return_value="localhost"))
        stack.enter_context(
            patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
        )
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
        remote_units_data={
            0: {
                "address": json.dumps("localhost"),
                "juju_topology": json.dumps(
                    {"application": "worker", "unit": "worker/0", "charm_name": "tempo"}
                ),
            }
        },
    )


@pytest.fixture(scope="function")
def remote_write():
    return Relation(
        "send-remote-write",
        remote_units_data={
            0: {"remote_write": json.dumps({"url": "http://prometheus:3000/api/write"})}
        },
    )


@pytest.fixture(scope="function")
def peer():
    return PeerRelation(
        endpoint=PEERS_RELATION_ENDPOINT_NAME,
        peers_data={1: {"fqdn": json.dumps("1.2.3.4")}},
    )


@pytest.fixture(scope="function")
def nginx_container():
    return Container(
        "nginx",
        execs={Exec(["update-ca-certificates", "--fresh"])},
        can_connect=True,
    )


@pytest.fixture(scope="function")
def nginx_prometheus_exporter_container():
    return Container(
        "nginx-prometheus-exporter",
        execs={Exec(["update-ca-certificates", "--fresh"])},
        can_connect=True,
    )
