from dataclasses import replace
from unittest.mock import MagicMock, patch

import ops
from scenario import PeerRelation, Relation, State


def test_monolithic_status_no_s3_no_workers(context):
    state_out = context.run(
        context.on.start(), State(unit_status=ops.ActiveStatus(), leader=True)
    )
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
            relations=[
                PeerRelation("peers", peers_data={1: {}, 2: {}}),
                s3,
                all_worker,
            ],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
            leader=True,
        ),
    )
    assert state_out.unit_status.name == "active"


def test_happy_status(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    state_out = context.run(
        context.on.start(),
        State(
            relations=[
                PeerRelation("peers", peers_data={1: {}, 2: {}}),
                s3,
                all_worker,
            ],
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
def test_k8s_patch_failed(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    state_out = context.run(
        context.on.update_status(),
        State(
            relations=[
                PeerRelation("peers", peers_data={1: {}, 2: {}}),
                s3,
                all_worker,
            ],
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
def test_k8s_patch_waiting(
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    state_out = context.run(
        context.on.config_changed(),
        State(
            relations=[
                PeerRelation("peers", peers_data={1: {}, 2: {}}),
                s3,
                all_worker,
            ],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
            leader=True,
        ),
    )
    assert state_out.unit_status == ops.WaitingStatus("waiting")


def test_multiple_ingresses_blocked(
    context,
    s3,
    nginx_container,
    nginx_prometheus_exporter_container,
    all_worker,
):
    """Test that charm is blocked when both traefik and istio ingresses are configured."""
    traefik_ingress = Relation(
        "ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "http"}
    )
    istio_ingress = Relation(
        "istio-ingress",
        remote_app_data={"external_host": "5.6.7.8", "tls_enabled": "False"},
    )
    state_out = context.run(
        context.on.update_status(),
        State(
            relations=[
                s3,
                all_worker,
                traefik_ingress,
                istio_ingress,
            ],
            containers=[nginx_container, nginx_prometheus_exporter_container],
            unit_status=ops.ActiveStatus(),
            leader=True,
        ),
    )
    assert state_out.unit_status == ops.BlockedStatus(
        "Multiple ingress relations are active ('ingress' and 'istio-ingress'). Remove one of the two."
    )


def test_metrics_generator(
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
