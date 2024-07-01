#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""
import logging
import socket
from subprocess import CalledProcessError, getoutput
from typing import Any, Dict, List, Optional, Sequence, Tuple

from charms.tempo_k8s.v2.tracing import (
    ReceiverProtocol,
    receiver_protocol_to_transport_protocol,
)
from charms.traefik_route_k8s.v0.traefik_route import TraefikRouteRequirer

import tempo_config

logger = logging.getLogger(__name__)


class Tempo:
    """Class representing the Tempo client workload configuration."""

    config_path = "/etc/tempo/tempo.yaml"

    # cert path on charm container
    server_cert_path = "/usr/local/share/ca-certificates/ca.crt"

    # cert paths on tempo container
    tls_cert_path = "/etc/tempo/tls/server.crt"
    tls_key_path = "/etc/tempo/tls/server.key"
    tls_ca_path = "/usr/local/share/ca-certificates/ca.crt"

    wal_path = "/etc/tempo/tempo_wal"
    log_path = "/var/log/tempo.log"
    tempo_ready_notice_key = "canonical.com/tempo/workload-ready"

    s3_relation_name = "s3"
    s3_bucket_name = "tempo"

    memberlist_port = 7946

    server_ports = {
        "tempo_http": 3200,
        "tempo_grpc": 9096,  # default grpc listen port is 9095, but that conflicts with promtail.
    }

    receiver_ports: Dict[str, int] = {
        "zipkin": 9411,
        "otlp_grpc": 4317,
        "otlp_http": 4318,
        "jaeger_thrift_http": 14268,
        # todo if necessary add support for:
        #  "kafka": 42,
        #  "jaeger_grpc": 14250,
        #  "opencensus": 43,
        #  "jaeger_thrift_compact": 44,
        #  "jaeger_thrift_binary": 45,
    }

    all_ports = {**server_ports, **receiver_ports}

    def __init__(
        self,
        external_host: Optional[str] = None,
        use_tls: bool = False,
    ):
        # ports source: https://github.com/grafana/tempo/blob/main/example/docker-compose/local/docker-compose.yaml

        # fqdn, if an ingress is not available, else the ingress address.
        self._external_hostname = external_host or socket.getfqdn()
        self.use_tls = use_tls

    @property
    def tempo_http_server_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.server_ports["tempo_http"]

    @property
    def tempo_grpc_server_port(self) -> int:
        """Return the receiver port for the built-in tempo_http protocol."""
        return self.server_ports["tempo_grpc"]

    def get_external_ports(self, service_name_prefix: str) -> List[Tuple[str, int, int]]:
        """List of service names and port mappings for the kubernetes service patch.

        Includes the tempo server as well as the receiver ports.
        """
        # todo allow remapping ports?
        all_ports = {**self.server_ports}
        return [
            (
                (f"{service_name_prefix}-{service_name}").replace("_", "-"),
                all_ports[service_name],
                all_ports[service_name],
            )
            for service_name in all_ports
        ]

    @property
    def url(self) -> str:
        """Base url at which the tempo server is locally reachable over http."""
        scheme = "https" if self.use_tls else "http"
        return f"{scheme}://{self._external_hostname}"

    def get_receiver_url(self, protocol: ReceiverProtocol, ingress: TraefikRouteRequirer):
        """Return the receiver endpoint URL based on the protocol.

        if ingress is used, return endpoint provided by the ingress instead.
        """
        protocol_type = receiver_protocol_to_transport_protocol.get(protocol)
        # ingress.is_ready returns True even when traefik hasn't sent any data yet
        has_ingress = ingress.is_ready() and ingress.external_host and ingress.scheme
        receiver_port = self.receiver_ports[protocol]

        if has_ingress:
            url = (
                ingress.external_host
                if protocol_type == "grpc"
                else f"{ingress.scheme}://{ingress.external_host}"
            )
        else:
            url = self._external_hostname if protocol_type == "grpc" else self.url

        return f"{url}:{receiver_port}"

    def _build_server_config(self):
        server_config = tempo_config.Server(
            http_listen_port=self.tempo_http_server_port,
            # we need to specify a grpc server port even if we're not using the grpc server,
            # otherwise it will default to 9595 and make promtail bork
            grpc_listen_port=self.tempo_grpc_server_port,
        )

        if self.use_tls:
            server_tls_config = tempo_config.TLS(
                cert_file=str(self.tls_cert_path),
                key_file=str(self.tls_key_path),
                client_ca_file=str(self.tls_ca_path),
            )
            server_config.http_tls_config = server_tls_config
            server_config.grpc_tls_config = server_tls_config

        return server_config

    def generate_config(
        self,
        receivers: Sequence[ReceiverProtocol],
        s3_config: dict,
        peers: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Generate the Tempo configuration.

        Only activate the provided receivers.
        """
        config = tempo_config.Tempo(
            auth_enabled=False,
            server=self._build_server_config(),
            distributor=self._build_distributor_config(receivers),
            ingester=self._build_ingester_config(),
            memberlist=self._build_memberlist_config(peers),
            compactor=self._build_compactor_config(),
            querier=self._build_querier_config(),
            storage=self._build_storage_config(s3_config),
        )

        if self.use_tls:
            # cfr:
            # https://grafana.com/docs/tempo/latest/configuration/network/tls/#client-configuration
            tls_config = {
                "tls_enabled": True,
                "tls_cert_path": self.tls_cert_path,
                "tls_key_path": self.tls_key_path,
                "tls_ca_path": self.tls_ca_path,
                # try with fqdn?
                "tls_server_name": self._external_hostname,
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

        return config.model_dump()

    def _build_storage_config(self, s3_config: dict):
        storage_config = tempo_config.TraceStorage(
            # where to store the wal locally
            wal=tempo_config.Wal(path=self.wal_path),
            pool=tempo_config.Pool(
                # number of traces per index record
                max_workers=400,
                queue_depth=20000,
            ),
            backend="s3",
            s3=tempo_config.S3(
                bucket=s3_config["bucket"],
                access_key=s3_config["access-key"],
                endpoint=s3_config["endpoint"],
                secret_key=s3_config["secret-key"],
            ),
            block=tempo_config.Block(version="v2"),
        )
        return tempo_config.Storage(trace=storage_config)

    def is_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        if self.use_tls:
            tls, s = f" --cacert {self.server_cert_path}", "s"
        else:
            tls = s = ""

        # cert is for fqdn/ingress, not for IP
        cmd = f"curl{tls} http{s}://{self._external_hostname}:{self.tempo_http_server_port}/ready"

        try:
            out = getoutput(cmd).split("\n")[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == "ready"

    def _build_querier_config(self):
        """Build querier config"""
        # TODO this won't work for distributed coordinator where query frontend will be on a different unit
        return tempo_config.Querier(
            frontend_worker=tempo_config.FrontendWorker(
                frontend_address=f"localhost:{self.tempo_grpc_server_port}"
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
                block_retention="720h",
                compacted_block_retention="1h",
                v2_out_buffer_bytes=5242880,
            )
        )

    def _build_memberlist_config(self, peers: Optional[List[str]]) -> tempo_config.Memberlist:
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

    def _build_distributor_config(self, receivers: Sequence[ReceiverProtocol]):  # noqa: C901
        """Build distributor config"""
        # receivers: the receivers we have to enable because the requirers we're related to
        # intend to use them. It already includes receivers that are always enabled
        # through config or because *this charm* will use them.
        receivers_set = set(receivers)

        if not receivers_set:
            logger.warning("No receivers set. Tempo will be up but not functional.")

        if self.use_tls:
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
        if "opencensus" in receivers_set:
            config["opencensus"] = receiver_config

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
