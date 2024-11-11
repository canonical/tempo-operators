import json
import socket

from charms.tempo_coordinator_k8s.v0.tracing import (
    ProtocolType,
    Receiver,
    TracingProviderAppData,
    TracingRequirerAppData,
)
from scenario import Relation, State

from charm import TempoCoordinatorCharm


def test_receivers_with_no_relations_or_config(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):

    state = State(
        leader=True,
        relations=[s3, all_worker],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    context.run(context.on.action("list-receivers"), state)
    assert context.action_results == {
        "otlp-http": f"http://{socket.getfqdn()}:4318",
        "jaeger-thrift-http": f"http://{socket.getfqdn()}:14268",
    }


def test_receivers_with_relations(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    tracing = Relation(
        "tracing",
        remote_app_data=TracingRequirerAppData(receivers=["otlp_grpc"]).dump(),
    )
    state = State(
        leader=True,
        relations=[s3, all_worker, tracing],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(tracing), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        # extra receivers should only include default otlp_http
        assert charm.enabled_receivers == {"otlp_http", "jaeger_thrift_http"}
        out = mgr.run()

    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.remote_app_data == TracingRequirerAppData(receivers=["otlp_grpc"]).dump()
    # provider app data should include endpoints for otlp_grpc and otlp_http
    provider_data = json.loads(tracing_out.local_app_data.get("receivers"))
    assert len(provider_data) == 3

    # run action
    context.run(context.on.action("list-receivers"), state)
    assert context.action_results == {
        "otlp-http": f"http://{socket.getfqdn()}:4318",
        "otlp-grpc": f"{socket.getfqdn()}:4317",
        "jaeger-thrift-http": f"http://{socket.getfqdn()}:14268",
    }


def test_receivers_with_relations_and_config(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    tracing = Relation(
        "tracing",
        local_app_data=TracingProviderAppData(
            receivers=[
                Receiver(
                    protocol=ProtocolType(name="otlp_grpc", type="grpc"),
                    url=f"{socket.getfqdn()}:4317",
                ),
                Receiver(
                    protocol=ProtocolType(name="otlp_http", type="http"),
                    url=f"{socket.getfqdn()}:4318",
                ),
            ]
        ).dump(),
        remote_app_data=TracingRequirerAppData(receivers=["otlp_grpc"]).dump(),
    )
    # start with a state that has config changed
    state = State(
        config={"always_enable_zipkin": True},
        leader=True,
        relations=[s3, all_worker, tracing],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.config_changed(), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        # extra receivers should only include default otlp_http
        assert charm.enabled_receivers == {"otlp_http", "zipkin", "jaeger_thrift_http"}

    # run action
    context.run(context.on.action("list-receivers"), state)
    assert context.action_results == {
        "otlp-http": f"http://{socket.getfqdn()}:4318",
        "zipkin": f"http://{socket.getfqdn()}:9411",
        "otlp-grpc": f"{socket.getfqdn()}:4317",
        "jaeger-thrift-http": f"http://{socket.getfqdn()}:14268",
    }
