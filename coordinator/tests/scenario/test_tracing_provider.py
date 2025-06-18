import pytest
from scenario import Relation, State

from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from charms.tempo_coordinator_k8s.v0.tracing import TracingProviderAppData


@pytest.mark.parametrize("leader", (True, False))
def test_receiver_api(
        context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container, leader
):
    # GIVEN two incoming tracing relations asking for otel grpc and http respectively
    tracing_grpc = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_grpc"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
                         '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}] '
        },
    )
    tracing_http = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_http"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
                         '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}] '
        },
    )

    state = State(
        leader=leader,
        relations=[tracing_grpc, tracing_http, s3, all_worker],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    with charm_tracing_disabled():
        # WHEN any event occurs
        with context(context.on.update_status(), state) as mgr:
            charm = mgr.charm
            assert charm._requested_receivers() == ("otlp_grpc", "otlp_http")
            state_out = mgr.run()

    # THEN both protocols are in the receivers published in the databag (local side)

    r_out = [r for r in state_out.relations if r.id == tracing_http.id][0]
    assert sorted(
        [r.protocol.name for r in TracingProviderAppData.load(r_out.local_app_data).receivers]
    ) == ["otlp_grpc", "otlp_http"]


def test_leader_removes_receivers_on_relation_broken(
        context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    # GIVEN two incoming tracing relations asking for otel grpc and http respectively
    tracing_grpc = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_grpc"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
                         '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}] '
        },
    )
    tracing_http = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_http"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
                         '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}] '
        },
    )

    state = State(
        leader=True,
        relations=[tracing_grpc, tracing_http, s3, all_worker],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    # WHEN the charm receives a relation-broken event for the one asking for otlp_grpc
    with charm_tracing_disabled():
        with context(context.on.relation_broken(tracing_grpc), state) as mgr:
            charm = mgr.charm
            assert charm._requested_receivers() == ("otlp_http",)
            state_out = mgr.run()

    # THEN otlp_grpc is gone from the databag
    r_out = [r for r in state_out.relations if r.id == tracing_http.id][0]
    assert sorted(
        [r.protocol.name for r in TracingProviderAppData.load(r_out.local_app_data).receivers]
    ) == ["otlp_http"]
