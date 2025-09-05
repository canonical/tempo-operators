import json
from dataclasses import replace
from unittest.mock import patch

import pytest
import yaml
from scenario import Relation, State

from tempo import Tempo


@pytest.fixture
def base_state(nginx_container, nginx_prometheus_exporter_container):
    return State(
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )


def test_external_url_present(context, base_state, s3, all_worker):
    # WHEN ingress is related with external_host
    tracing = Relation("tracing", remote_app_data={"receivers": "[]"})
    ingress = Relation(
        "ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "http"}
    )
    state = replace(base_state, relations=[tracing, ingress, s3, all_worker])

    out = context.run(context.on.relation_created(tracing), state)

    # THEN external_url is present in tracing relation databag
    tracing_out = out.get_relations(tracing.endpoint)[0]
    expected_data = [
        {
            "protocol": {"name": "otlp_http", "type": "http"},
            "url": "http://1.2.3.4:4318",
        },
    ]
    assert (
        sorted(
            json.loads(tracing_out.local_app_data["receivers"]),
            key=lambda x: x["protocol"]["name"],
        )
        == expected_data
    )


@pytest.mark.parametrize(
    "add_peer, expected_servers_count",
    ((True, 2),),
)
@patch("socket.getfqdn", lambda: "1.2.3.4")
def test_ingress_relation_set_with_dynamic_config(
    add_peer, expected_servers_count, context, base_state, s3, all_worker, peer
):
    # WHEN ingress is related with external_host
    ingress = Relation(
        "ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "http"}
    )

    state = replace(base_state, relations=[ingress, s3, all_worker])
    if add_peer:
        state = replace(base_state, relations=[ingress, s3, all_worker, peer])

    with patch("charm.TempoCoordinatorCharm.is_workload_ready", lambda _: False):
        out = context.run(context.on.relation_joined(ingress), state)

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
                f"juju-{state.model.name}-{charm_name}-jaeger-grpc": {
                    "entryPoints": ["jaeger-grpc"],
                    "rule": "ClientIP(`0.0.0.0/0`)",
                    "service": f"juju-{state.model.name}-{charm_name}-service-jaeger-grpc",
                },
            },
            "services": {
                f"juju-{state.model.name}-{charm_name}-service-jaeger-thrift-http": {
                    "loadBalancer": {
                        "servers": [
                            {"url": "http://1.2.3.4:14268"}
                            for server in range(expected_servers_count)
                        ]
                    }
                },
                f"juju-{state.model.name}-{charm_name}-service-otlp-http": {
                    "loadBalancer": {
                        "servers": [
                            {"url": "http://1.2.3.4:4318"}
                            for server in range(expected_servers_count)
                        ]
                    }
                },
                f"juju-{state.model.name}-{charm_name}-service-tempo-http": {
                    "loadBalancer": {
                        "servers": [
                            {"url": "http://1.2.3.4:3200"}
                            for server in range(expected_servers_count)
                        ]
                    }
                },
                f"juju-{state.model.name}-{charm_name}-service-zipkin": {
                    "loadBalancer": {
                        "servers": [
                            {"url": "http://1.2.3.4:9411"}
                            for server in range(expected_servers_count)
                        ]
                    }
                },
                f"juju-{state.model.name}-{charm_name}-service-otlp-grpc": {
                    "loadBalancer": {
                        "servers": [
                            {"url": "h2c://1.2.3.4:4317"}
                            for server in range(expected_servers_count)
                        ]
                    },
                },
                f"juju-{state.model.name}-{charm_name}-service-tempo-grpc": {
                    "loadBalancer": {
                        "servers": [
                            {"url": "h2c://1.2.3.4:9096"}
                            for server in range(expected_servers_count)
                        ]
                    }
                },
                f"juju-{state.model.name}-{charm_name}-service-jaeger-grpc": {
                    "loadBalancer": {
                        "servers": [
                            {"url": "h2c://1.2.3.4:14250"}
                            for server in range(expected_servers_count)
                        ]
                    }
                },
            },
        },
    }

    # THEN dynamic config is present in ingress relation
    ingress_out = out.get_relations(ingress.endpoint)[0]
    assert ingress_out.local_app_data
    assert yaml.safe_load(ingress_out.local_app_data["config"]) == expected_rel_data


@patch("charm.TempoCoordinatorCharm.is_workload_ready", lambda _: False)
def test_ingress_config_middleware_tls(context, base_state, s3, all_worker):
    charm_name = "tempo-coordinator-k8s"
    # GIVEN an ingress relation with TLS
    ingress = Relation(
        "ingress", remote_app_data={"external_host": "1.2.3.4", "scheme": "https"}
    )

    state = replace(base_state, relations=[ingress, s3, all_worker])

    # WHEN relation is joined
    out = context.run(context.on.relation_joined(ingress), state)

    # THEN middleware config is present in ingress config
    ingress_out = out.get_relations(ingress.endpoint)[0]
    assert ingress_out.local_app_data
    config = yaml.safe_load(ingress_out.local_app_data["config"])
    middlewares = config["http"]["middlewares"]
    for proto_name, port in Tempo.all_ports.items():
        middleware = f"juju-{state.model.name}-{charm_name}-middleware-{proto_name.replace('_', '-')}"
        assert middleware in middlewares
        assert middlewares[middleware] == {
            "redirectScheme": {
                "permanent": True,
                "port": port,
                "scheme": "https",
            }
        }
