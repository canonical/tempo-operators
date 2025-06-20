# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import pathlib
from contextlib import ExitStack
from shutil import rmtree
from unittest.mock import MagicMock, patch

import pytest
from coordinated_workers.interfaces.cluster import (
    ClusterRequirerAppData,
    ClusterRequirerUnitData,
)
from interface_tester import InterfaceTester
from ops import ActiveStatus
from ops.pebble import Layer
from scenario import Relation
from scenario.state import Container, PeerRelation, State

from charm import TempoCoordinatorCharm

nginx_container = Container(
    name="nginx",
    can_connect=True,
    layers={
        "foo": Layer(
            {
                "summary": "foo",
                "description": "bar",
                "services": {
                    "nginx": {
                        "startup": "enabled",
                        "current": "active",
                        "name": "nginx",
                    }
                },
                "checks": {},
            }
        )
    },
)

nginx_prometheus_exporter_container = Container(
    name="nginx-prometheus-exporter",
    can_connect=True,
)

s3_relation = Relation(
    "s3",
    remote_app_data={
        "access-key": "key",
        "bucket": "tempo",
        "endpoint": "http://1.2.3.4:9000",
        "secret-key": "soverysecret",
    },
)
cluster_relation = Relation(
    "tempo-cluster",
    remote_app_data=ClusterRequirerAppData(role="all").dump(),
    remote_units_data={
        0: ClusterRequirerUnitData(
            address="http://example.com",
            juju_topology={"application": "app", "unit": "unit", "charm_name": "charmname"},
        ).dump()
    },
)

grafana_source_relation = Relation(
    "grafana-source",
    remote_app_data={"datasources": json.dumps({"tempo/0": {"type": "tempo", "uid": "01234"}})},
)

peers = PeerRelation("peers", peers_data={1: {}})

k8s_resource_patch_ready = MagicMock(return_value=True)


@pytest.fixture(autouse=True, scope="module")
def patch_all():
    with ExitStack() as stack:
        stack.enter_context(patch("lightkube.core.client.GenericSyncClient"))
        stack.enter_context(
            patch.multiple(
                "charms.observability_libs.v0.kubernetes_compute_resources_patch.KubernetesComputeResourcesPatch",
                _namespace="test-namespace",
                _patch=lambda _: None,
                is_ready=k8s_resource_patch_ready,
                get_status=lambda _: ActiveStatus(""),
            )
        )
        yield

        # cleanup: some tests create a spurious src folder for alert rules in ./
        src_root = pathlib.Path(__file__).parent / "src"
        if src_root.exists():
            rmtree(src_root)


# Interface tests are centrally hosted at https://github.com/canonical/charm-relation-interfaces.
# this fixture is used by the test runner of charm-relation-interfaces to test tempo's compliance
# with the interface specifications.
# DO NOT MOVE OR RENAME THIS FIXTURE! If you need to, you'll need to open a PR on
# https://github.com/canonical/charm-relation-interfaces and change tempo's test configuration
# to include the new identifier/location.
@pytest.fixture
def cluster_tester(interface_tester: InterfaceTester):
    interface_tester.configure(
        charm_type=TempoCoordinatorCharm,
        state_template=State(
            leader=True,
            containers=[nginx_container, nginx_prometheus_exporter_container],
            relations=[peers, s3_relation],
        ),
    )
    yield interface_tester


@pytest.fixture
def tracing_tester(interface_tester: InterfaceTester):
    interface_tester.configure(
        charm_type=TempoCoordinatorCharm,
        state_template=State(
            leader=True,
            containers=[nginx_container, nginx_prometheus_exporter_container],
            relations=[peers, s3_relation, cluster_relation],
        ),
    )
    yield interface_tester


@pytest.fixture
def s3_tester(interface_tester: InterfaceTester):
    interface_tester.configure(
        charm_type=TempoCoordinatorCharm,
        state_template=State(
            leader=True,
            containers=[nginx_container, nginx_prometheus_exporter_container],
            relations=[peers, cluster_relation],
        ),
    )
    yield interface_tester


@pytest.fixture
def grafana_datasource_tester(interface_tester: InterfaceTester):
    interface_tester.configure(
        charm_type=TempoCoordinatorCharm,
        state_template=State(
            leader=True,
            containers=[nginx_container, nginx_prometheus_exporter_container],
            relations=[peers, s3_relation, cluster_relation],
        ),
    )
    yield interface_tester


@pytest.fixture
def grafana_datasource_exchange_tester(interface_tester: InterfaceTester):
    interface_tester.configure(
        charm_type=TempoCoordinatorCharm,
        state_template=State(
            leader=True,
            containers=[nginx_container, nginx_prometheus_exporter_container],
            relations=[peers, s3_relation, cluster_relation, grafana_source_relation],
        ),
    )
    yield interface_tester
