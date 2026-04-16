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
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['jaeger_grpc']}"
                        },
                        "thrift_http": {
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['jaeger_thrift_http']}"
                        },
                    }
                },
                "zipkin": {"endpoint": f"0.0.0.0:{Tempo.receiver_ports['zipkin']}"},
                "otlp": {
                    "protocols": {
                        "http": {
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['otlp_http']}"
                        },
                        "grpc": {
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['otlp_grpc']}"
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
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['jaeger_thrift_http']}",
                            **tls_config,
                        },
                        "grpc": {
                            "endpoint": "0.0.0.0:14250",
                            **tls_config,
                        },
                    }
                },
                "zipkin": {
                    "endpoint": f"0.0.0.0:{Tempo.receiver_ports['zipkin']}",
                    **tls_config,
                },
                "otlp": {
                    "protocols": {
                        "http": {
                            "endpoint": f"0.0.0.0:{Tempo.receiver_ports['otlp_http']}",
                            **tls_config,
                        },
                        "grpc": {"endpoint": "0.0.0.0:4317", **tls_config},
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
    "addresses, expected_frontend_address",
    (
        # No query-frontend: use localhost
        (
            {},
            f"localhost:{Tempo.tempo_grpc_server_port}",
        ),
        # All workers have role 'all' (querier == query-frontend addresses):
        # use localhost so each querier connects to its own co-located query-frontend
        (
            {
                "querier": {"worker-0.svc-a.ns.svc.cluster.local", "worker2-0.svc-b.ns.svc.cluster.local"},
                "query-frontend": {"worker-0.svc-a.ns.svc.cluster.local", "worker2-0.svc-b.ns.svc.cluster.local"},
            },
            f"localhost:{Tempo.tempo_grpc_server_port}",
        ),
        # Single worker with role 'all': use localhost
        (
            {
                "querier": {"worker-0.svc-a.ns.svc.cluster.local"},
                "query-frontend": {"worker-0.svc-a.ns.svc.cluster.local"},
            },
            f"localhost:{Tempo.tempo_grpc_server_port}",
        ),
        # Dedicated query-frontend workers: use the service FQDN
        (
            {
                "querier": {"worker-querier-0.svc-q.ns.svc.cluster.local"},
                "query-frontend": {"worker-qf-0.svc-qf.ns.svc.cluster.local"},
            },
            f"svc-qf.ns.svc.cluster.local:{Tempo.tempo_grpc_server_port}",
        ),
        # Mixed: one 'all' worker and one dedicated querier - use service FQDN
        # because not all queriers have a co-located query-frontend
        (
            {
                "querier": {"worker-all-0.svc-a.ns.svc.cluster.local", "worker-q-0.svc-q.ns.svc.cluster.local"},
                "query-frontend": {"worker-all-0.svc-a.ns.svc.cluster.local"},
            },
            f"svc-a.ns.svc.cluster.local:{Tempo.tempo_grpc_server_port}",
        ),
    ),
)
def test_tempo_querier_config(addresses, expected_frontend_address):
    assert (
        Tempo(720, lambda: [])
        ._build_querier_config(addresses)
        .frontend_worker.frontend_address
        == expected_frontend_address
    )


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
