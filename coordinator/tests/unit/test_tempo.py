import pytest

import tempo_config
from tempo import Tempo

tls_config = {
    "tls": {
        "ca_file": "/usr/local/share/ca-certificates/ca.crt",
        "cert_file": "/etc/worker/server.cert",
        "key_file": "/etc/worker/private.key",
    }
}


@pytest.mark.parametrize(
    "use_tls, expected_config",
    (
        (
            False,
            {
                "jaeger": {
                    "protocols": {
                        "grpc": {
                            "endpoint": f"localhost:{Tempo.receiver_ports['jaeger_grpc']}"
                        },
                        "thrift_http": {
                            "endpoint": f"localhost:{Tempo.receiver_ports['jaeger_thrift_http']}"
                        },
                    }
                },
                "zipkin": {"endpoint": f"localhost:{Tempo.receiver_ports['zipkin']}"},
                "otlp": {
                    "protocols": {
                        "http": {
                            "endpoint": f"localhost:{Tempo.receiver_ports['otlp_http']}"
                        },
                        "grpc": {
                            "endpoint": f"localhost:{Tempo.receiver_ports['otlp_grpc']}"
                        },
                    }
                },
            },
        ),
        (
            True,
            {
                "jaeger": {
                    "protocols": {
                        "thrift_http": {
                            "endpoint": f"localhost:{Tempo.receiver_ports['jaeger_thrift_http']}",
                            **tls_config,
                        },
                        "grpc": {
                            "endpoint": "localhost:14250",
                            **tls_config,
                        },
                    }
                },
                "zipkin": {
                    "endpoint": f"localhost:{Tempo.receiver_ports['zipkin']}",
                    **tls_config,
                },
                "otlp": {
                    "protocols": {
                        "http": {
                            "endpoint": f"localhost:{Tempo.receiver_ports['otlp_http']}",
                            **tls_config,
                        },
                        "grpc": {"endpoint": "localhost:4317", **tls_config},
                    }
                },
            },
        ),
    ),
)
def test_tempo_distributor_config(use_tls, expected_config):
    assert (
        Tempo(720, lambda: [])._build_distributor_config(use_tls).receivers
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
    assert Tempo(720, lambda: [])._build_memberlist_config(peers) == expected_config


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
        Tempo(720, lambda: [])
        ._build_ingester_config(addresses)
        .lifecycler.ring.replication_factor
        == expected_replication
    )
