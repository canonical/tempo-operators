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
                        "grpc": None,
                        "thrift_http": None,
                    }
                },
                "zipkin": None,
                "otlp": {"protocols": {"http": None, "grpc": None}},
            },
        ),
        (
            ("otlp_http", "zipkin", "tempo", "jaeger_thrift_http"),
            False,
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": None,
                    }
                },
                "zipkin": None,
                "otlp": {"protocols": {"http": None}},
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
                                "cert_file": "/etc/tempo/tls/server.crt",
                                "key_file": "/etc/tempo/tls/server.key",
                            }
                        },
                    }
                },
                "zipkin": {
                    "tls": {
                        "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                        "cert_file": "/etc/tempo/tls/server.crt",
                        "key_file": "/etc/tempo/tls/server.key",
                    }
                },
                "otlp": {
                    "protocols": {
                        "http": {
                            "tls": {
                                "ca_file": "/usr/local/share/ca-certificates/ca.crt",
                                "cert_file": "/etc/tempo/tls/server.crt",
                                "key_file": "/etc/tempo/tls/server.key",
                            }
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
        Tempo(None, use_tls=use_tls)._build_distributor_config(protocols).receivers
        == expected_config
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
    assert Tempo()._build_memberlist_config(peers) == expected_config
