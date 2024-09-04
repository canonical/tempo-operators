#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""
import logging
from typing import Any, Callable, Dict, List, Optional, Sequence, Set, Tuple

import yaml
from charms.tempo_k8s.v2.tracing import ReceiverProtocol
from cosl.coordinated_workers.coordinator import Coordinator

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

    s3_bucket_name = "tempo"

    memberlist_port = 7946

    server_ports: Dict[str, int] = {
        "tempo_http": 3200,
        "tempo_grpc": 9096,  # default grpc listen port is 9095, but that conflicts with promtail.
    }

    # ports defined are the default ports specified in
    # https://github.com/open-telemetry/opentelemetry-collector-contrib/tree/main/receiver
    # for each of the below receivers.
    receiver_ports: Dict[str, int] = {
        "zipkin": 9411,
        "otlp_grpc": 4317,
        "otlp_http": 4318,
        "jaeger_thrift_http": 14268,
        "jaeger_grpc": 14250,
    }

    all_ports = {**server_ports, **receiver_ports}

    def __init__(
        self,
        requested_receivers: Callable[[], "Tuple[ReceiverProtocol, ...]"],
        retention_period_hours: int,
    ):
        self._receivers_getter = requested_receivers
        self._retention_period_hours = retention_period_hours

    @property
    def tempo_http_server_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.server_ports["tempo_http"]

    @property
    def tempo_grpc_server_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.server_ports["tempo_grpc"]

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
            distributor=self._build_distributor_config(
                self._receivers_getter(), coordinator.tls_available
            ),
            ingester=self._build_ingester_config(),
            memberlist=self._build_memberlist_config(coordinator.cluster.gather_addresses()),
            compactor=self._build_compactor_config(),
            querier=self._build_querier_config(coordinator.cluster.gather_addresses_by_role()),
            storage=self._build_storage_config(coordinator._s3_config),
            metrics_generator=self._build_metrics_generator_config(
                coordinator.remote_write_endpoints_getter(), coordinator.tls_available  # type: ignore
            ),
        )

        if config.metrics_generator:
            config.overrides = self._build_overrides_config()

        if coordinator.tls_available:
            # cfr:
            # https://grafana.com/docs/tempo/latest/configuration/network/tls/#client-configuration
            tls_config = {
                "tls_enabled": True,
                "tls_cert_path": self.tls_cert_path,
                "tls_key_path": self.tls_key_path,
                "tls_ca_path": self.tls_ca_path,
                # try with fqdn?
                "tls_server_name": coordinator.hostname,
            }
            config.ingester_client = tempo_config.Client(
                grpc_client_config=tempo_config.ClientTLS(**tls_config)
            )
            config.metrics_generator_client = tempo_config.Client(
                grpc_client_config=tempo_config.ClientTLS(**tls_config)
            )
            config.querier.frontend_worker.grpc_client_config = tempo_config.ClientTLS(
                **tls_config
            )
            config.memberlist = config.memberlist.model_copy(update=tls_config)

        return yaml.dump(config.model_dump(mode="json", by_alias=True, exclude_none=True))

    def _build_overrides_config(self):
        return tempo_config.Overrides(
            defaults=tempo_config.Defaults(
                metrics_generator=tempo_config.MetricsGeneratorDefaults(
                    processors=[tempo_config.MetricsGeneratorProcessor.SPAN_METRICS],
                )
            )
        )

    def _build_metrics_generator_config(
        self, remote_write_endpoints: List[Dict[str, Any]], use_tls=False
    ):
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
            storage=tempo_config.MetricsGeneratorStorage(
                path=self.metrics_generator_wal_path,
                remote_write=remote_write_instances,
            )
            # Adding juju topology will be done on the worker's side
            # to populate the correct unit label.
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
        """Build querier config"""
        # if distributor and query-frontend have the same address, then the mode of operation is 'all'.
        query_frontend_addresses = roles_addresses.get(tempo_config.TempoRole.query_frontend)
        distributor_addresses = roles_addresses.get(tempo_config.TempoRole.distributor)

        if not query_frontend_addresses or query_frontend_addresses == distributor_addresses:
            addr = "localhost"
        else:
            addr = query_frontend_addresses.pop()

        return tempo_config.Querier(
            frontend_worker=tempo_config.FrontendWorker(
                frontend_address=f"{addr}:{self.tempo_grpc_server_port}"
            ),
        )

    def _build_compactor_config(self):
        """Build compactor config"""
        return tempo_config.Compactor(
            compaction=tempo_config.Compaction(
                # blocks in this time window will be compacted together
                compaction_window="1h",
                # maximum size of compacted blocks
                max_compaction_objects=1000000,
                # total trace retention
                block_retention=f"{self._retention_period_hours}h",
                compacted_block_retention="1h",
            )
        )

    def _build_memberlist_config(self, peers: Optional[Set[str]]) -> tempo_config.Memberlist:
        """Build memberlist config"""
        return tempo_config.Memberlist(
            abort_if_cluster_join_fails=False,
            bind_port=self.memberlist_port,
            join_members=([f"{peer}:{self.memberlist_port}" for peer in peers] if peers else []),
        )

    def _build_ingester_config(self):
        """Build ingester config"""
        # the length of time after a trace has not received spans to consider it complete and flush it
        # cut the head block when it hits this number of traces or ...
        #   this much time passes
        return tempo_config.Ingester(
            trace_idle_period="10s",
            max_block_bytes=100,
            max_block_duration="30m",
        )

    def _build_distributor_config(
        self, receivers: Sequence[ReceiverProtocol], use_tls=False
    ):  # noqa: C901
        """Build distributor config"""
        # receivers: the receivers we have to enable because the requirers we're related to
        # intend to use them. It already includes receivers that are always enabled
        # through config or because *this charm* will use them.
        receivers_set = set(receivers)

        if not receivers_set:
            logger.warning("No receivers set. Tempo will be up but not functional.")

        if use_tls:
            receiver_config = {
                "tls": {
                    "ca_file": str(self.tls_ca_path),
                    "cert_file": str(self.tls_cert_path),
                    "key_file": str(self.tls_key_path),
                }
            }
        else:
            receiver_config = None

        config = {}

        if "zipkin" in receivers_set:
            config["zipkin"] = receiver_config

        otlp_config = {}
        if "otlp_http" in receivers_set:
            otlp_config["http"] = receiver_config
        if "otlp_grpc" in receivers_set:
            otlp_config["grpc"] = receiver_config
        if otlp_config:
            config["otlp"] = {"protocols": otlp_config}

        jaeger_config = {}
        if "jaeger_thrift_http" in receivers_set:
            jaeger_config["thrift_http"] = receiver_config
        if "jaeger_grpc" in receivers_set:
            jaeger_config["grpc"] = receiver_config
        if "jaeger_thrift_binary" in receivers_set:
            jaeger_config["thrift_binary"] = receiver_config
        if "jaeger_thrift_compact" in receivers_set:
            jaeger_config["thrift_compact"] = receiver_config
        if jaeger_config:
            config["jaeger"] = {"protocols": jaeger_config}

        return tempo_config.Distributor(receivers=config)
