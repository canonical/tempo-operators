#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""

import logging
import re
from collections import defaultdict
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import yaml
from charms.tempo_coordinator_k8s.v0.tracing import ReceiverProtocol
from coordinated_workers.coordinator import Coordinator

import tempo_config

logger = logging.getLogger(__name__)


class Tempo:
    """Class representing the Tempo client workload configuration."""

    # cert paths on tempo container
    tls_cert_path = "/etc/worker/server.cert"
    tls_key_path = "/etc/worker/private.key"
    tls_ca_path = "/usr/local/share/ca-certificates/ca.crt"

    wal_path = "/etc/tempo/tempo_wal"
    metrics_generator_wal_path = "/etc/tempo/metrics_generator_wal"

    # this is the single source of truth for which ports are opened and configured
    # in the distributed Tempo deployment
    memberlist_port = 7946
    tempo_http_server_port = 3200
    tempo_grpc_server_port = (
        9096  # default grpc listen port is 9095, but that conflicts with promtail.
    )

    # these are the default ports specified in
    # https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver
    # for each of the below receivers.
    zipkin_receiver_port = 9411
    otlp_grpc_receiver_port = 4317
    otlp_http_receiver_port = 4318
    jaeger_thrift_http_receiver_port = 14268
    jaeger_grpc_receiver_port = 14250

    # utility grouping of the ports
    receiver_ports: Dict[ReceiverProtocol, int] = {
        "zipkin": zipkin_receiver_port,
        "otlp_grpc": otlp_grpc_receiver_port,
        "otlp_http": otlp_http_receiver_port,
        "jaeger_thrift_http": jaeger_thrift_http_receiver_port,
        "jaeger_grpc": jaeger_grpc_receiver_port,
    }
    server_ports: Dict[str, int] = {
        "tempo_http": tempo_http_server_port,
        "tempo_grpc": tempo_grpc_server_port,
    }
    all_ports = {
        "tempo_http": tempo_http_server_port,
        "tempo_grpc": tempo_grpc_server_port,
        **receiver_ports,
    }

    def __init__(
        self,
        retention_period_hours: int,
        remote_write_endpoints: Callable[[], List[Dict[str, Any]]],
    ):
        self._retention_period_hours = retention_period_hours
        self._remote_write_endpoints_getter = remote_write_endpoints

    def config(
        self,
        coordinator: Coordinator,
    ) -> str:
        """Generate the Tempo configuration.

        Only activate the provided receivers.
        """
        config = tempo_config.TempoConfig(
            auth_enabled=False,
            server=self._build_server_config(coordinator.tls_available),
            distributor=self._build_distributor_config(coordinator.tls_available),
            ingester=self._build_ingester_config(
                coordinator.cluster.gather_addresses_by_role()
            ),
            memberlist=self._build_memberlist_config(
                coordinator.cluster.gather_addresses()
            ),
            compactor=self._build_compactor_config(),
            querier=self._build_querier_config(
                coordinator.cluster.gather_addresses_by_role()
            ),
            storage=self._build_storage_config(coordinator._s3_config),
            metrics_generator=self._build_metrics_generator_config(
                coordinator.tls_available,
            ),
        )

        if config.metrics_generator:
            config.overrides = self._build_overrides_config()

        if coordinator.tls_available:
            tls_config = self._build_tls_config(coordinator.cluster.gather_addresses())

            config.ingester_client = tempo_config.Client(
                grpc_client_config=tempo_config.ClientTLS(**tls_config)
            )
            config.metrics_generator_client = tempo_config.Client(
                grpc_client_config=tempo_config.ClientTLS(**tls_config)
            )

            config.querier.frontend_worker.grpc_client_config = tempo_config.ClientTLS(
                **tls_config,
            )

            config.memberlist = config.memberlist.model_copy(update=tls_config)

        return yaml.dump(
            config.model_dump(mode="json", by_alias=True, exclude_none=True)
        )

    def _build_tls_config(self, workers_addrs: Tuple[str, ...]):
        """Build TLS config to be used by Tempo's internal clients to communicate with each other."""

        # cfr:
        # https://grafana.com/docs/tempo/latest/configuration/network/tls/#client-configuration
        return {
            "tls_enabled": True,
            "tls_cert_path": self.tls_cert_path,
            "tls_key_path": self.tls_key_path,
            "tls_ca_path": self.tls_ca_path,
            # Tempo's internal components contact each other using their IPs not their DNS names
            # and we don't provide IP sans to Tempo's certificate. So, we need to provide workers' DNS names
            # as tls_server_name to verify the certificate against this name not against the IP.
            "tls_server_name": workers_addrs[0] if len(workers_addrs) > 0 else "",
        }

    def _build_overrides_config(self):
        # in order to tell tempo to enable the metrics generator, we need to set
        # "processors": list of enabled processors in the overrides section
        return tempo_config.Overrides(
            defaults=tempo_config.Defaults(
                metrics_generator=tempo_config.MetricsGeneratorDefaults(
                    processors=[
                        tempo_config.MetricsGeneratorProcessorLabel.SPAN_METRICS,
                        tempo_config.MetricsGeneratorProcessorLabel.SERVICE_GRAPHS,
                    ],
                )
            )
        )

    def _build_metrics_generator_config(self, use_tls=False):
        remote_write_endpoints = self._remote_write_endpoints_getter()
        if len(remote_write_endpoints) == 0:
            return None

        # Assumptions:
        #  1) Same CA is used for Prometheus and ingressed Tempo and whatever running in the same cos-lite model.
        #  2) To send those metrics over TLS, Prometheus and ingressed Tempo both should have TLS enabled.
        #  Enabling TLS on Prometheus only will result in failure to send those metrics.
        if use_tls:
            for endpoint in remote_write_endpoints:
                endpoint["tls_config"] = {
                    "ca_file": self.tls_ca_path,
                }

        remote_write_instances = [
            tempo_config.RemoteWrite(**endpoint) for endpoint in remote_write_endpoints
        ]

        config = tempo_config.MetricsGenerator(
            ring=self._build_ring(),
            storage=tempo_config.MetricsGeneratorStorage(
                path=self.metrics_generator_wal_path,
                remote_write=remote_write_instances,
            ),
            # Adding juju topology will be done on the worker's side
            # to populate the correct unit label.
            processor=tempo_config.MetricsGeneratorProcessor(
                span_metrics=tempo_config.MetricsGeneratorSpanMetricsProcessor(),
                service_graphs=tempo_config.MetricsGeneratorServiceGraphsProcessor(),
            ),
            # per-processor configuration should go in here
        )
        return config

    def _build_server_config(self, use_tls=False):
        server_config = tempo_config.Server(
            http_listen_port=self.tempo_http_server_port,
            # we need to specify a grpc server port even if we're not using the grpc server,
            # otherwise it will default to 9595 and make promtail bork
            grpc_listen_port=self.tempo_grpc_server_port,
        )

        if use_tls:
            server_tls_config = tempo_config.TLS(
                cert_file=str(self.tls_cert_path),
                key_file=str(self.tls_key_path),
                client_ca_file=str(self.tls_ca_path),
            )
            server_config.http_tls_config = server_tls_config
            server_config.grpc_tls_config = server_tls_config

        return server_config

    def _build_storage_config(self, s3_config: dict):
        storage_config = tempo_config.TraceStorage(
            # where to store the wal locally
            wal=tempo_config.Wal(path=self.wal_path),  # type: ignore
            pool=tempo_config.Pool(
                # number of traces per index record
                max_workers=400,
                queue_depth=20000,
            ),
            backend="s3",
            s3=tempo_config.S3(**s3_config),
            # starting from Tempo 2.4, we need to use at least parquet v3 to have search capabilities (Grafana support)
            # https://grafana.com/docs/tempo/latest/release-notes/v2-4/#vparquet3-is-now-the-default-block-format
            block=tempo_config.Block(version="vParquet3"),
        )
        return tempo_config.Storage(trace=storage_config)

    def _build_querier_config(self, roles_addresses: Dict[str, Set[str]]):
        """Build querier config.

        Use query-frontend workers' service fqdn to loadbalance across query-frontend worker instances if any.
        """
        query_frontend_addresses = roles_addresses.get(
            tempo_config.TempoRole.query_frontend
        )
        if not query_frontend_addresses:
            svc_addr = "localhost"
        else:
            addresses = sorted(query_frontend_addresses)
            query_frontend_addr = next(iter(addresses))
            # remove "tempo-worker-0." from "tempo-worker-0.tempo-endpoints.cluster.local.svc"
            # to extract the worker's headless service
            svc_addr = re.sub(r"^[^.]+\.", "", query_frontend_addr)
        return tempo_config.Querier(
            frontend_worker=tempo_config.FrontendWorker(
                frontend_address=f"{svc_addr}:{self.tempo_grpc_server_port}"
            ),
        )

    def _build_compactor_config(self):
        """Build compactor config"""
        return tempo_config.Compactor(
            ring=self._build_ring(),
            compaction=tempo_config.Compaction(
                # blocks in this time window will be compacted together
                compaction_window="1h",
                # maximum size of compacted blocks
                max_compaction_objects=1000000,
                # total trace retention
                block_retention=f"{self._retention_period_hours}h",
                compacted_block_retention="1h",
            ),
        )

    def _build_memberlist_config(
        self, peers: Optional[Tuple[str, ...]]
    ) -> tempo_config.Memberlist:
        """Build memberlist config"""
        return tempo_config.Memberlist(
            abort_if_cluster_join_fails=False,
            bind_port=self.memberlist_port,
            join_members=(
                [f"{peer}:{self.memberlist_port}" for peer in peers] if peers else []
            ),
        )

    def _build_ingester_config(self, roles_addresses: Dict[str, Set[str]]):
        """Build ingester config"""
        ingester_addresses = roles_addresses.get(tempo_config.TempoRole.ingester)
        # the length of time after a trace has not received spans to consider it complete and flush it
        # cut the head block when it hits this number of traces or ...
        #   this much time passes
        return tempo_config.Ingester(
            trace_idle_period="10s",
            max_block_bytes=100,
            max_block_duration="30m",
            # replication_factor=3 to ensure that the Tempo cluster can still be
            # functional if one of the ingesters is down.
            lifecycler=tempo_config.Lifecycler(
                ring=tempo_config.IngesterRing(
                    kvstore=self._build_memberlist_kvstore(),
                    replication_factor=(
                        3 if ingester_addresses and len(ingester_addresses) >= 3 else 1
                    ),
                ),
            ),
        )

    def _build_distributor_config(self, use_tls=False):  # noqa: C901
        """Build distributor config"""
        # We enable all receivers. We'll only open ports for the ones that we know are actually
        # going to be used though; for security reasons.
        receivers_set: Set[ReceiverProtocol] = set(self.receiver_ports)

        if not receivers_set:
            logger.warning("No receivers set. Tempo will be up but not functional.")

        def _build_receiver_config(
            protocol: ReceiverProtocol,
        ) -> Dict[str, Any]:
            return {
                # set this to localhost because all receivers are enabled by default, so someone
                # might push traces directly to the worker pods using the pod IPs.
                "endpoint": f"localhost:{self.receiver_ports[protocol]}",
                **(
                    {
                        "tls": {
                            "ca_file": str(self.tls_ca_path),
                            "cert_file": str(self.tls_cert_path),
                            "key_file": str(self.tls_key_path),
                        }
                    }
                    if use_tls
                    else {}
                ),
            }

        config: Dict[str, Any] = defaultdict(lambda: defaultdict(dict))

        if "zipkin" in receivers_set:
            config["zipkin"] = _build_receiver_config("zipkin")

        for proto, config_field_name in {("otlp_http", "http"), ("otlp_grpc", "grpc")}:
            if proto in receivers_set:
                config["otlp"]["protocols"][config_field_name] = _build_receiver_config(
                    proto
                )

        for proto, config_field_name in {
            ("jaeger_thrift_http", "thrift_http"),
            ("jaeger_grpc", "grpc"),
        }:
            if proto in receivers_set:
                config["jaeger"]["protocols"][config_field_name] = (
                    _build_receiver_config(proto)
                )

        return tempo_config.Distributor(ring=self._build_ring(), receivers=config)

    def _build_memberlist_kvstore(self):
        # explicitly set store to memberlist for the compactor, distributor, ingester, and metrics-generator
        # if not set, they may default to a different store, fail to join the ring, and cause silent issues.
        return tempo_config.Kvstore(store="memberlist")

    def _build_ring(self):
        return tempo_config.Ring(kvstore=self._build_memberlist_kvstore())
