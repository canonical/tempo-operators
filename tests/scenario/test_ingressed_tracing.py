from unittest.mock import patch

import pytest
import yaml
from charms.tempo_k8s.v1.charm_tracing import charm_tracing_disabled
from scenario import Relation, State


@pytest.fixture
def base_state(nginx_container, nginx_prometheus_exporter_container):
    return State(
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )


def test_external_url_present(context, base_state, s3, all_worker):
    # WHEN ingress is related with external_host
    tracing = Relation("tracing", remote_app_data={"receivers": "[]"})
    ingress = Relation("ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "http"})
    state = base_state.replace(relations=[tracing, ingress, s3, all_worker])

    with charm_tracing_disabled():
        out = context.run(getattr(tracing, "created_event"), state)

    # THEN external_url is present in tracing relation databag
    tracing_out = out.get_relations(tracing.endpoint)[0]
    assert tracing_out.local_app_data == {
        "receivers": '[{"protocol": {"name": "otlp_http", "type": "http"}, "url": "http://1.2.3.4:4318"}]',
    }


@patch("socket.getfqdn", lambda: "1.2.3.4")
def test_ingress_relation_set_with_dynamic_config(context, base_state, s3, all_worker):
    # WHEN ingress is related with external_host
    ingress = Relation("ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "http"})
    state = base_state.replace(relations=[ingress, s3, all_worker])

    with patch("charm.TempoCoordinatorCharm.is_workload_ready", lambda _: False):
        out = context.run(ingress.joined_event, state)

    charm_name = "tempo-coordinator-k8s"

    expected_rel_data = {
        "http": {
            "routers": {
                f"juju-{state.model.name}-{charm_name}-jaeger-thrift-http": {
                    "entryPoints": ["jaeger-thrift-http"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-jaeger-thrift-http",
                },
                f"juju-{state.model.name}-{charm_name}-otlp-http": {
                    "entryPoints": ["otlp-http"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-otlp-http",
                },
                f"juju-{state.model.name}-{charm_name}-tempo-http": {
                    "entryPoints": ["tempo-http"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-tempo-http",
                },
                f"juju-{state.model.name}-{charm_name}-zipkin": {
                    "entryPoints": ["zipkin"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-zipkin",
                },
                f"juju-{state.model.name}-{charm_name}-otlp-grpc": {
                    "entryPoints": ["otlp-grpc"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-otlp-grpc",
                },
                f"juju-{state.model.name}-{charm_name}-tempo-grpc": {
                    "entryPoints": ["tempo-grpc"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-tempo-grpc",
                },
                f"juju-{state.model.name}-{charm_name}-opencensus": {
                    "entryPoints": ["opencensus"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-opencensus",
                },
                f"juju-{state.model.name}-{charm_name}-jaeger-grpc": {
                    "entryPoints": ["jaeger-grpc"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-jaeger-grpc",
                },
            },
            "services": {
                f"juju-{state.model.name}-{charm_name}-service-jaeger-thrift-http": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:14268"}]}
                },
                f"juju-{state.model.name}-{charm_name}-service-otlp-http": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:4318"}]}
                },
                f"juju-{state.model.name}-{charm_name}-service-tempo-http": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:3200"}]}
                },
                f"juju-{state.model.name}-{charm_name}-service-zipkin": {
                    "loadBalancer": {"servers": [{"url": "http://1.2.3.4:9411"}]}
                },
                f"juju-{state.model.name}-{charm_name}-service-otlp-grpc": {
                    "loadBalancer": {"servers": [{"url": "h2c://1.2.3.4:4317"}]},
                },
                f"juju-{state.model.name}-{charm_name}-service-tempo-grpc": {
                    "loadBalancer": {"servers": [{"url": "h2c://1.2.3.4:9096"}]}
                },
                f"juju-{state.model.name}-{charm_name}-service-opencensus": {
                    "loadBalancer": {"servers": [{"url": "h2c://1.2.3.4:55678"}]}
                },
                f"juju-{state.model.name}-{charm_name}-service-jaeger-grpc": {
                    "loadBalancer": {"servers": [{"url": "h2c://1.2.3.4:14250"}]}
                },
            },
        },
    }

    # THEN dynamic config is present in ingress relation
    ingress_out = out.get_relations(ingress.endpoint)[0]
    assert ingress_out.local_app_data
    assert yaml.safe_load(ingress_out.local_app_data["config"]) == expected_rel_data
