# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Any, Dict, List, Optional, Set, cast

import crossplane
from charms.tempo_coordinator_k8s.v0.tracing import (
    ReceiverProtocol,
    TransportProtocolType,
    receiver_protocol_to_transport_protocol,
)
from cosl.coordinated_workers.coordinator import Coordinator
from cosl.coordinated_workers.nginx import CERT_PATH, KEY_PATH

from tempo import Tempo
from tempo_config import TempoRole

logger = logging.getLogger(__name__)


class NginxConfig:
    """Helper class to manage the nginx workload."""

    def __init__(self, server_name: str):
        self.server_name = server_name

    def config(self, coordinator: Coordinator) -> str:
        """Build and return the Nginx configuration."""
        full_config = self._prepare_config(coordinator)
        return crossplane.build(full_config)

    def _prepare_config(self, coordinator: Coordinator) -> List[dict]:
        log_level = "error"
        addresses_by_role = coordinator.cluster.gather_addresses_by_role()
        # build the complete configuration
        full_config = [
            {"directive": "worker_processes", "args": ["5"]},
            {"directive": "error_log", "args": ["/dev/stderr", log_level]},
            {"directive": "pid", "args": ["/tmp/nginx.pid"]},
            {"directive": "worker_rlimit_nofile", "args": ["8192"]},
            {
                "directive": "events",
                "args": [],
                "block": [{"directive": "worker_connections", "args": ["4096"]}],
            },
            {
                "directive": "http",
                "args": [],
                "block": [
                    # upstreams (load balancing)
                    *self._upstreams(addresses_by_role),
                    # temp paths
                    {"directive": "client_body_temp_path", "args": ["/tmp/client_temp"]},
                    {"directive": "proxy_temp_path", "args": ["/tmp/proxy_temp_path"]},
                    {"directive": "fastcgi_temp_path", "args": ["/tmp/fastcgi_temp"]},
                    {"directive": "uwsgi_temp_path", "args": ["/tmp/uwsgi_temp"]},
                    {"directive": "scgi_temp_path", "args": ["/tmp/scgi_temp"]},
                    # logging
                    {"directive": "default_type", "args": ["application/octet-stream"]},
                    {
                        "directive": "log_format",
                        "args": [
                            "main",
                            '$remote_addr - $remote_user [$time_local]  $status "$request" $body_bytes_sent "$http_referer" "$http_user_agent" "$http_x_forwarded_for"',
                        ],
                    },
                    *self._log_verbose(verbose=False),
                    # tempo-related
                    {"directive": "sendfile", "args": ["on"]},
                    {"directive": "tcp_nopush", "args": ["on"]},
                    *self._resolver(custom_resolver=None),
                    # TODO: add custom http block for the user to config?
                    {
                        "directive": "map",
                        "args": ["$http_x_scope_orgid", "$ensured_x_scope_orgid"],
                        "block": [
                            {"directive": "default", "args": ["$http_x_scope_orgid"]},
                            {"directive": "", "args": ["anonymous"]},
                        ],
                    },
                    {"directive": "proxy_read_timeout", "args": ["300"]},
                    # server block
                    *self._build_servers_config(
                        addresses_by_role, coordinator.nginx.are_certificates_on_disk
                    ),
                ],
            },
        ]
        return full_config

    def _log_verbose(self, verbose: bool = True) -> List[Dict[str, Any]]:
        if verbose:
            return [{"directive": "access_log", "args": ["/dev/stderr", "main"]}]
        return [
            {
                "directive": "map",
                "args": ["$status", "$loggable"],
                "block": [
                    {"directive": "~^[23]", "args": ["0"]},
                    {"directive": "default", "args": ["1"]},
                ],
            },
            {"directive": "access_log", "args": ["/dev/stderr"]},
        ]

    def _upstreams(self, addresses_by_role: Dict[str, Set[str]]) -> List[Dict[str, Any]]:
        addresses_mapped_to_upstreams = {}
        nginx_upstreams = []
        addresses_mapped_to_upstreams = addresses_by_role.copy()
        if TempoRole.distributor in addresses_mapped_to_upstreams.keys():
            nginx_upstreams.extend(
                self._distributor_upstreams(addresses_mapped_to_upstreams[TempoRole.distributor])
            )
        if TempoRole.query_frontend in addresses_mapped_to_upstreams.keys():
            nginx_upstreams.extend(
                self._query_frontend_upstreams(
                    addresses_mapped_to_upstreams[TempoRole.query_frontend]
                )
            )

        return nginx_upstreams

    def _distributor_upstreams(self, address_set: Set[str]) -> List[Dict[str, Any]]:
        upstreams = []
        for protocol, port in Tempo.receiver_ports.items():
            upstreams.append(self._upstream(protocol.replace("_", "-"), address_set, port))
        return upstreams

    def _query_frontend_upstreams(self, address_set: Set[str]) -> List[Dict[str, Any]]:
        upstreams = []
        for protocol, port in Tempo.server_ports.items():
            upstreams.append(self._upstream(protocol.replace("_", "-"), address_set, port))
        return upstreams

    def _upstream(self, role: str, address_set: Set[str], port: int) -> Dict[str, Any]:
        return {
            "directive": "upstream",
            "args": [role],
            "block": [{"directive": "server", "args": [f"{addr}:{port}"]} for addr in address_set],
        }

    def _locations(self, upstream: str, grpc: bool, tls: bool) -> List[Dict[str, Any]]:
        s = "s" if tls else ""
        protocol = f"grpc{s}" if grpc else f"http{s}"
        nginx_locations = [
            {
                "directive": "location",
                "args": ["/"],
                "block": [
                    {
                        "directive": "grpc_pass" if grpc else "proxy_pass",
                        "args": [f"{protocol}://{upstream}"],
                    },
                    # if a server is down, no need to wait for a long time to pass on the request to the next available server
                    {
                        "directive": "proxy_connect_timeout",
                        "args": ["5s"],
                    },
                ],
            }
        ]
        return nginx_locations

    def _resolver(self, custom_resolver: Optional[List[Any]] = None) -> List[Dict[str, Any]]:
        if custom_resolver:
            return [{"directive": "resolver", "args": [custom_resolver]}]
        return [{"directive": "resolver", "args": ["kube-dns.kube-system.svc.cluster.local."]}]

    def _basic_auth(self, enabled: bool) -> List[Optional[Dict[str, Any]]]:
        if enabled:
            return [
                {"directive": "auth_basic", "args": ['"Tempo"']},
                {
                    "directive": "auth_basic_user_file",
                    "args": ["/etc/nginx/secrets/.htpasswd"],
                },
            ]
        return []

    def _listen(self, port: int, ssl: bool, http2: bool) -> List[Dict[str, Any]]:
        directives = []
        directives.append(
            {"directive": "listen", "args": self._listen_args(port, False, ssl, http2)}
        )
        directives.append(
            {"directive": "listen", "args": self._listen_args(port, True, ssl, http2)}
        )
        return directives

    def _listen_args(self, port: int, ipv6: bool, ssl: bool, http2: bool) -> List[str]:
        args = []
        if ipv6:
            args.append(f"[::]:{port}")
        else:
            args.append(f"{port}")
        if ssl:
            args.append("ssl")
        if http2:
            args.append("http2")
        return args

    def _build_servers_config(
        self, addresses_by_role: Dict[str, Set[str]], tls: bool = False
    ) -> List[Dict[str, Any]]:
        servers = []
        roles = addresses_by_role.keys()
        # generate a server config for receiver protocols (9411, 4317, 4318, 14268, 14250)
        if TempoRole.distributor.value in roles:
            for protocol, port in Tempo.receiver_ports.items():
                servers.append(
                    self._build_server_config(
                        port, protocol.replace("_", "-"), self._is_protocol_grpc(protocol), tls
                    )
                )
        # generate a server config for the Tempo server protocols (3200, 9096)
        if TempoRole.query_frontend.value in roles:
            for protocol, port in Tempo.server_ports.items():
                servers.append(
                    self._build_server_config(
                        port, protocol.replace("_", "-"), self._is_protocol_grpc(protocol), tls
                    )
                )
        return servers

    def _build_server_config(
        self, port: int, upstream: str, grpc: bool = False, tls: bool = False
    ) -> Dict[str, Any]:
        auth_enabled = False

        if tls:
            return {
                "directive": "server",
                "args": [],
                "block": [
                    *self._listen(port, ssl=True, http2=grpc),
                    *self._basic_auth(auth_enabled),
                    {
                        "directive": "proxy_set_header",
                        "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                    },
                    # FIXME: use a suitable SERVER_NAME
                    {"directive": "server_name", "args": [self.server_name]},
                    {"directive": "ssl_certificate", "args": [CERT_PATH]},
                    {"directive": "ssl_certificate_key", "args": [KEY_PATH]},
                    {"directive": "ssl_protocols", "args": ["TLSv1", "TLSv1.1", "TLSv1.2"]},
                    {"directive": "ssl_ciphers", "args": ["HIGH:!aNULL:!MD5"]},  # codespell:ignore
                    *self._locations(upstream, grpc, tls),
                ],
            }

        return {
            "directive": "server",
            "args": [],
            "block": [
                *self._listen(port, ssl=False, http2=grpc),
                *self._basic_auth(auth_enabled),
                {
                    "directive": "proxy_set_header",
                    "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                },
                {"directive": "server_name", "args": [self.server_name]},
                *self._locations(upstream, grpc, tls),
            ],
        }

    def _is_protocol_grpc(self, protocol: str) -> bool:
        """
        Return True if the given protocol is gRPC
        """
        if (
            protocol == "tempo_grpc"
            or receiver_protocol_to_transport_protocol.get(cast(ReceiverProtocol, protocol))
            == TransportProtocolType.grpc
        ):
            return True
        return False
