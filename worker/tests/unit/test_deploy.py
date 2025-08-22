# Copyright 2022 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import socket
from functools import partial
from unittest.mock import patch, MagicMock
import yaml
import pytest
from coordinated_workers.interfaces.cluster import (
    ClusterRequirerAppData,
    ClusterRequirer,
)
from ops.model import ActiveStatus
from scenario import Container, Relation, State, Mount

from tests.unit.conftest import (
    TEMPO_VERSION_EXEC_OUTPUT,
    _urlopen_patch,
    UPDATE_CA_CERTS_EXEC_OUTPUT,
)
from tests.unit.helpers import set_role
from coordinated_workers.worker import CONFIG_FILE
from cosl.juju_topology import JujuTopology


@pytest.fixture(autouse=True)
def patch_urllib_request():
    with patch("urllib.request.urlopen", new=partial(_urlopen_patch, resp="ready")):
        yield


@pytest.fixture(autouse=True)
def patch_stop():
    with patch("ops.model.Container.stop"):
        yield


tempo_container = Container(
    "tempo",
    can_connect=True,
    execs={
        TEMPO_VERSION_EXEC_OUTPUT,
        UPDATE_CA_CERTS_EXEC_OUTPUT,
    },
)


@pytest.mark.parametrize(
    "role",
    [
        "all",
        "querier",
        "query-frontend",
        "ingester",
        "distributor",
        "compactor",
        "metrics-generator",
    ],
)
@pytest.mark.parametrize(
    "workload_tracing_receivers, expected_env",
    (
        (None, {}),
        (
            {"otlp_http": "1.2.3.4", "jaeger_thrift_http": "5.6.7.8"},
            {
                "OTEL_RESOURCE_ATTRIBUTES": "juju_application=worker,juju_model=test"
                + ",juju_model_uuid=00000000-0000-4000-8000-000000000000,juju_unit=worker/0,juju_charm=tempo",
                "OTEL_TRACES_EXPORTER": "otlp",
                "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": "1.2.3.4/v1/traces",
            },
        ),
        (
            {"jaeger_thrift_http": "1.2.3.4"},
            {},
        ),
        (
            {"zipkin": "1.2.3.4"},
            {},
        ),
    ),
)
@patch.object(
    ClusterRequirer, "get_worker_config", MagicMock(return_value={"config": "config"})
)
@patch.object(
    JujuTopology,
    "from_charm",
    MagicMock(
        return_value=JujuTopology(
            model="test",
            model_uuid="00000000-0000-4000-8000-000000000000",
            application="worker",
            unit="worker/0",
            charm_name="tempo",
        )
    ),
)
def test_pebble_ready_plan(ctx, workload_tracing_receivers, expected_env, role):
    host = socket.getfqdn()
    expected_plan = {
        "checks": {
            "ready": {
                "http": {"url": f"http://{host}:3200/ready"},
                "override": "replace",
                "threshold": 3,
            }
        },
        "services": {
            "tempo": {
                "override": "replace",
                "summary": "tempo worker process",
                "command": f"/bin/tempo -config.file=/etc/worker/config.yaml -target {role if role != 'all' else 'scalable-single-binary'}",
                "startup": "enabled",
                "environment": {
                    k: "" for k in ("http_proxy", "https_proxy", "no_proxy")
                },
            }
        },
    }

    if expected_env:
        expected_plan["services"]["tempo"]["environment"].update(expected_env)

    state_out = ctx.run(
        ctx.on.pebble_ready(tempo_container),
        state=set_role(
            State(
                containers=[tempo_container],
                relations=[
                    Relation(
                        "tempo-cluster",
                        remote_app_data={
                            "worker_config": json.dumps("beef"),
                            "remote_write_endpoints": json.dumps(
                                [{"url": "http://test:3000"}]
                            ),
                            "workload_tracing_receivers": json.dumps(
                                workload_tracing_receivers
                            ),
                        },
                    )
                ],
            ),
            role,
        ),
    )

    tempo_container_out = state_out.get_container(tempo_container.name)
    assert tempo_container_out.services.get("tempo").is_running() is True
    assert tempo_container_out.plan.to_dict() == expected_plan

    if role == "all":
        assert state_out.unit_status == ActiveStatus("(all roles) ready.")
    else:
        assert state_out.unit_status == ActiveStatus(f"{role} ready.")


@pytest.mark.parametrize(
    "role_str, expected",
    (
        ("all", "all"),
        ("querier", "querier"),
        ("query-frontend", "query-frontend"),
        ("ingester", "ingester"),
        ("distributor", "distributor"),
        ("compactor", "compactor"),
        ("metrics-generator", "metrics-generator"),
    ),
)
def test_role(ctx, role_str, expected):
    out = ctx.run(
        ctx.on.config_changed(),
        state=set_role(
            State(
                leader=True,
                containers=[tempo_container],
                relations=[Relation("tempo-cluster")],
            ),
            role_str,
        ),
    )
    if expected:
        data = ClusterRequirerAppData.load(
            out.get_relations("tempo-cluster")[0].local_app_data
        )
        assert data.role == expected
    else:
        assert not out.get_relations("tempo-cluster")[0].local_app_data


@pytest.mark.parametrize(
    "role_str",
    (
        ("all"),
        ("metrics-generator"),
    ),
)
@patch.object(JujuTopology, "from_charm")
def test_config_juju_topology(topology_mock, role_str, ctx, tmp_path):
    charm_topo = JujuTopology(
        model="test",
        model_uuid="00000000-0000-4000-8000-000000000000",
        application="worker",
        unit="worker/0",
        charm_name="tempo",
    )
    topology_mock.return_value = charm_topo
    cfg_file = tmp_path / "fake.config"
    data = yaml.safe_dump({"metrics_generator": {}})
    cfg_file.write_text("some: yaml")

    cluster_relation = Relation(
        "tempo-cluster",
        remote_app_data={
            "worker_config": json.dumps(data),
            "remote_write_endpoints": json.dumps(
                [{"url": "http://prometheus:3000/push"}]
            ),
        },
    )

    state = State(
        leader=True,
        containers=[
            Container(
                "tempo",
                can_connect=True,
                mounts={"cfg": Mount(location=CONFIG_FILE, source=cfg_file)},
                execs={
                    UPDATE_CA_CERTS_EXEC_OUTPUT,
                },
            )
        ],
        relations=[cluster_relation],
    )

    ctx.run(ctx.on.relation_changed(cluster_relation), state=set_role(state, role_str))

    # assert that topology is added to config
    updated_config = yaml.safe_load(cfg_file.read_text())
    expected_labels = {
        "juju_model": "test",
        "juju_model_uuid": "00000000-0000-4000-8000-000000000000",
        "juju_application": "worker",
        "juju_unit": "worker/0",
        "juju_charm_name": "tempo",
    }
    assert updated_config == {
        "metrics_generator": {"registry": {"external_labels": expected_labels}}
    }
