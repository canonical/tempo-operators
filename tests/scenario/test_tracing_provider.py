from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from charms.tempo_coordinator_k8s.v0.tracing import TracingProviderAppData
from scenario import Relation, State


def test_receivers_removed_on_relation_broken(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    tracing_grpc = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_grpc"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
            '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}, '
        },
    )
    tracing_http = Relation(
        "tracing",
        remote_app_data={"receivers": '["otlp_http"]'},
        local_app_data={
            "receivers": '[{"protocol": {"name": "otlp_grpc", "type": "grpc"} , "url": "foo.com:10"}, '
            '{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://foo.com:11"}, '
        },
    )

    state = State(
        leader=True,
        relations=[tracing_grpc, tracing_http, s3, all_worker],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    with charm_tracing_disabled():
        with context(context.on.relation_broken(tracing_grpc), state) as mgr:
            charm = mgr.charm
            assert charm._requested_receivers() == ("jaeger_thrift_http", "otlp_http")
            state_out = mgr.run()

    r_out = [r for r in state_out.relations if r.id == tracing_http.id][0]
    # "otlp_grpc" is gone from the databag
    assert sorted(
        [r.protocol.name for r in TracingProviderAppData.load(r_out.local_app_data).receivers]
    ) == [
        "jaeger_thrift_http",
        "otlp_http",
    ]
