#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Tempo workload configuration and client."""
import logging
import re
import socket
from subprocess import CalledProcessError, getoutput
from typing import Dict, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

from charms.tempo_k8s.v2.tracing import (
    ReceiverProtocol,
    receiver_protocol_to_transport_protocol,
)
from charms.traefik_route_k8s.v0.traefik_route import TraefikRouteRequirer

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

    _tls_min_version = ""
    # cfr https://grafana.com/docs/enterprise-traces/latest/configure/reference/#supported-contents-and-default-values
    # "VersionTLS12"

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
        enable_receivers: Optional[Sequence[ReceiverProtocol]] = None,
        use_tls: bool = False,
    ):
        # ports source: https://github.com/grafana/tempo/blob/main/example/docker-compose/local/docker-compose.yaml

        # fqdn, if an ingress is not available, else the ingress address.
        self._external_hostname = external_host or socket.getfqdn()
        self.enabled_receivers = enable_receivers or []
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
        server_config = {
            "http_listen_port": self.tempo_http_server_port,
            # we need to specify a grpc server port even if we're not using the grpc server,
            # otherwise it will default to 9595 and make promtail bork
            "grpc_listen_port": self.tempo_grpc_server_port,
        }
        if self.use_tls:
            for cfg in ("http_tls_config", "grpc_tls_config"):
                server_config[cfg] = {  # type: ignore
                    "cert_file": str(self.tls_cert_path),
                    "key_file": str(self.tls_key_path),
                    "client_ca_file": str(self.tls_ca_path),
                    "client_auth_type": "VerifyClientCertIfGiven",
                }
            server_config["tls_min_version"] = self._tls_min_version  # type: ignore

        return server_config

    def generate_config(
        self,
        receivers: Sequence[ReceiverProtocol],
        s3_config: dict,
        peers: Optional[List[str]] = None,
    ) -> dict:
        """Generate the Tempo configuration.

        Only activate the provided receivers.
        """
        config = {
            "auth_enabled": False,
            "server": self._build_server_config(),
            # more configuration information can be found at
            # https://github.com/open-telemetry/opentelemetry-collector/tree/overlord/receiver
            "distributor": {"receivers": self._build_receivers_config(receivers)},
            # the length of time after a trace has not received spans to consider it complete and flush it
            # cut the head block when it hits this number of traces or ...
            #   this much time passes
            "ingester": {
                "trace_idle_period": "10s",
                "max_block_bytes": 100,
                "max_block_duration": "30m",
            },
            "memberlist": {
                "abort_if_cluster_join_fails": False,
                "bind_port": self.memberlist_port,
                "join_members": (
                    [f"{peer}:{self.memberlist_port}" for peer in peers] if peers else []
                ),
            },
            "compactor": {
                "compaction": {
                    # blocks in this time window will be compacted together
                    "compaction_window": "1h",
                    # maximum size of compacted blocks
                    "max_compaction_objects": 1000000,
                    # total trace retention
                    "block_retention": "720h",
                    "compacted_block_retention": "1h",
                    "v2_out_buffer_bytes": 5242880,
                }
            },
            # TODO this won't work for distributed coordinator where query frontend will be on a different unit
            "querier": {
                "frontend_worker": {"frontend_address": f"localhost:{self.tempo_grpc_server_port}"}
            },
            # see https://grafana.com/docs/tempo/latest/configuration/#storage
            "storage": self._build_storage_config(s3_config),
        }

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
            config["ingester_client"] = {"grpc_client_config": tls_config}
            config["metrics_generator_client"] = {"grpc_client_config": tls_config}

            config["querier"]["frontend_worker"].update({"grpc_client_config": tls_config})

            # this is not an error.
            config["memberlist"].update(tls_config)

        return config

    def _build_storage_config(self, s3_config: dict):
        storage_config = {
            "wal": {
                # where to store the wal locally
                "path": self.wal_path
            },
            "pool": {
                # number of traces per index record
                "max_workers": 400,
                "queue_depth": 20000,
            },
            "backend": "s3",
            "s3": {
                "bucket": s3_config["bucket"],
                "access_key": s3_config["access-key"],
                # remove scheme to avoid "Endpoint url cannot have fully qualified paths." on Tempo startup
                "endpoint": re.sub(
                    rf"^{urlparse(s3_config['endpoint']).scheme}://",
                    "",
                    s3_config["endpoint"],
                ),
                "secret_key": s3_config["secret-key"],
                "insecure": (False if s3_config["endpoint"].startswith("https://") else True),
            },
        }
        return {"trace": storage_config}

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

    def _build_receivers_config(self, receivers: Sequence[ReceiverProtocol]):  # noqa: C901
        # receivers: the receivers we have to enable because the requirers we're related to
        # intend to use them
        # it already includes self.enabled_receivers: receivers we have to enable because *this charm* will use them.
        receivers_set = set(receivers)

        if not receivers_set:
            logger.warning("No receivers set. Tempo will be up but not functional.")

        if self.use_tls:
            receiver_config = {
                "tls": {
                    "ca_file": str(self.tls_ca_path),
                    "cert_file": str(self.tls_cert_path),
                    "key_file": str(self.tls_key_path),
                    "min_version": self._tls_min_version,
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

        return config
