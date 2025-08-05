from dataclasses import replace
from unittest.mock import MagicMock, patch

import ops
from scenario import PeerRelation, Relation, State


def test_monolithic_status_no_s3_no_workers(context):
    state_out = context.run(context.on.start(), State(unit_status=ops.ActiveStatus(), leader=True))
    assert state_out.unit_status.name == "blocked"


def test_scaled_status_no_s3(context, all_worker):
    state_out = context.run(
        context.on.start(),
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}})],
            unit_status=ops.ActiveStatus(),
        ),
    )
    assert state_out.unit_status.name == "blocked"


def test_scaled_status_no_workers(context, all_worker):
    state_out = context.run(
        context.on.start(),
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}})],
            unit_status=ops.ActiveStatus(),
        ),
    )
    assert state_out.unit_status.name == "blocked"


def test_scaled_status_with_s3_and_workers(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    state_out = context.run(
        context.on.start(),
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}}), s3, all_worker],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
            leader=True,
        ),
    )
    assert state_out.unit_status.name == "active"


@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_happy_status(
    workload_ready_mock,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    state_out = context.run(
        context.on.start(),
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}}), s3, all_worker],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
            leader=True,
        ),
    )
    assert state_out.unit_status.name == "active"


@patch(
    "coordinated_workers.coordinator.KubernetesComputeResourcesPatch.get_status",
    MagicMock(return_value=ops.BlockedStatus("`juju trust` this application")),
)
@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_k8s_patch_failed(
    workload_ready_mock,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    state_out = context.run(
        context.on.update_status(),
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}}), s3, all_worker],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
            leader=True,
        ),
    )
    assert state_out.unit_status == ops.BlockedStatus("`juju trust` this application")


@patch(
    "coordinated_workers.coordinator.KubernetesComputeResourcesPatch.get_status",
    MagicMock(return_value=ops.WaitingStatus("waiting")),
)
@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_k8s_patch_waiting(
    workload_ready_mock,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    state_out = context.run(
        context.on.config_changed(),
        State(
            relations=[PeerRelation("peers", peers_data={1: {}, 2: {}}), s3, all_worker],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
            leader=True,
        ),
    )
    assert state_out.unit_status == ops.WaitingStatus("waiting")


@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_metrics_generator(
    workload_ready_mock,
    context,
    s3,
    nginx_container,
    nginx_prometheus_exporter_container,
    all_worker,
    remote_write,
):
    metrics_gen_worker = Relation(
        "tempo-cluster",
        remote_app_data={"role": '"metrics-generator"'},
    )
    state = State(
        relations=[
            PeerRelation("peers", peers_data={1: {}, 2: {}}),
            s3,
            all_worker,
            metrics_gen_worker,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        unit_status=ops.ActiveStatus(),
        leader=True,
    )
    state_out = context.run(context.on.relation_changed(metrics_gen_worker), state)
    assert state_out.unit_status.name == "active"
    assert "metrics-generator disabled" in state_out.unit_status.message

    state = replace(
        state,
        relations=[
            PeerRelation("peers", peers_data={1: {}, 2: {}}),
            s3,
            all_worker,
            metrics_gen_worker,
            remote_write,
        ],
    )
    state_out = context.run(context.on.relation_changed(remote_write), state)
    assert state_out.unit_status.name == "active"
    assert "metrics-generator disabled" not in state_out.unit_status.message
