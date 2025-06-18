import pytest

import tempo_config
from tempo import Tempo


@pytest.mark.parametrize(
    "protocols, use_tls, expected_config",
    (
        (
            (
                "otlp_grpc",
                "otlp_http",
                "zipkin",
                "tempo",
                "jaeger_http_thrift",
                "jaeger_grpc",
                "jaeger_thrift_http",
                "jaeger_thrift_http",
            ),
            False,
            {
                "jaeger": {
                    "protocols": {
                        "grpc": {"endpoint": f"0.0.0.0:{Tempo.receiver_ports['jaeger_grpc']}"},
                        "thrift_http": {
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['jaeger_thrift_http']}"
                        },
                    }
                },
                "zipkin": {"endpoint": f"0.0.0.0:{Tempo.receiver_ports['zipkin']}"},
                "otlp": {
                    "protocols": {
                        "http": {"endpoint": f"0.0.0.0:{Tempo.receiver_ports['otlp_http']}"},
                        "grpc": {"endpoint": f"0.0.0.0:{Tempo.receiver_ports['otlp_grpc']}"},
                    }
                },
            },
        ),
        (
            ("otlp_http", "zipkin", "tempo", "jaeger_thrift_http"),
            False,
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": {
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['jaeger_thrift_http']}"
                        },
                    }
                },
                "zipkin": {"endpoint": f"0.0.0.0:{Tempo.receiver_ports['zipkin']}"},
                "otlp": {
                    "protocols": {
                        "http": {"endpoint": f"0.0.0.0:{Tempo.receiver_ports['otlp_http']}"}
                    }
                },
            },
        ),
        (
            ("otlp_http", "zipkin", "tempo", "jaeger_thrift_http"),
            True,
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": {
                            "tls": {
                                "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                                "cert_file": "/etc/worker/server.cert",
                                "key_file": "/etc/worker/private.key",
                            },
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['jaeger_thrift_http']}",
                        },
                    }
                },
                "zipkin": {
                    "tls": {
                        "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                        "cert_file": "/etc/worker/server.cert",
                        "key_file": "/etc/worker/private.key",
                    },
                    "endpoint": f"0.0.0.0:{Tempo.receiver_ports['zipkin']}",
                },
                "otlp": {
                    "protocols": {
                        "http": {
                            "tls": {
                                "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                                "cert_file": "/etc/worker/server.cert",
                                "key_file": "/etc/worker/private.key",
                            },
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['otlp_http']}",
                        },
                    }
                },
            },
        ),
        ([], False, {}),
    ),
)
def test_tempo_distributor_config(protocols, use_tls, expected_config):
    assert (
        Tempo(None, 720)._build_distributor_config(protocols, use_tls).receivers == expected_config
    )


@pytest.mark.parametrize(
    "peers, expected_config",
    (
        (
            [],
            tempo_config.Memberlist(
                abort_if_cluster_join_fails=False, bind_port=7946, join_members=[]
            ),
        ),
        (
            ["peer1", "peer2"],
            tempo_config.Memberlist(
                abort_if_cluster_join_fails=False,
                bind_port=7946,
                join_members=["peer1:7946", "peer2:7946"],
            ),
        ),
    ),
)
def test_tempo_memberlist_config(peers, expected_config):
    assert Tempo(None, 720)._build_memberlist_config(peers) == expected_config


@pytest.mark.parametrize(
    "addresses, expected_replication",
    (
        (
            {"querier": {"addr1"}, "ingester": {"addr1", "addr2", "addr3"}},
            3,
        ),
        (
            {"querier": {"addr1"}},
            1,
        ),
        (
            {"ingester": {"addr2"}, "querier": {"addr1"}},
            1,
        ),
    ),
)
def test_tempo_ingester_config(addresses, expected_replication):
    assert (
        Tempo(None, 720)._build_ingester_config(addresses).lifecycler.ring.replication_factor
        == expected_replication
    )
