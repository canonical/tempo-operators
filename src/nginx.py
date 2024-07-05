# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Any, Dict, List, Optional, Set

import crossplane
from ops import CharmBase
from ops.pebble import Layer, PathError, ProtocolError


from tempo import Tempo
from tempo_cluster import TempoClusterProvider, TempoRole

logger = logging.getLogger(__name__)


NGINX_DIR = "/etc/nginx"
NGINX_CONFIG = f"{NGINX_DIR}/nginx.conf"
KEY_PATH = f"{NGINX_DIR}/certs/server.key"
CERT_PATH = f"{NGINX_DIR}/certs/server.cert"
CA_CERT_PATH = f"{NGINX_DIR}/certs/ca.cert"


class Nginx:
    """Helper class to manage the nginx workload."""

    config_path = NGINX_CONFIG

    def __init__(self, charm: CharmBase, cluster_provider: TempoClusterProvider, server_name: str):
        self._charm = charm
        self.cluster_provider = cluster_provider
        self.server_name = server_name
        self._container = self._charm.unit.get_container("nginx")

    def configure_pebble_layer(self, tls: bool) -> None:
        """Configure pebble layer."""
        new_config: str = self.config(tls)
        should_restart: bool = self._has_config_changed(new_config)
        if self._container.can_connect():
            self._container.push(
                self.config_path, self.config(tls=tls), make_dirs=True  # type: ignore
            )
            self._container.add_layer("nginx", self.layer, combine=True)
            self._container.autostart()

            if should_restart:
                logger.info("new nginx config: restarting the service")
                self.reload()

    def config(self, tls: bool = False) -> str:
        """Build and return the Nginx configuration."""
        full_config = self._prepare_config(tls)
        return crossplane.build(full_config)

    def _prepare_config(self, tls: bool = False) -> List[dict]:
        log_level = "error"
        addresses_by_role = self.cluster_provider.gather_addresses_by_role()
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
                    *self._servers(addresses_by_role, tls),
                ],
            },
        ]
        return full_config

    def _has_config_changed(self, new_config: str) -> bool:
        """Return True if the passed config differs from the one on disk."""
        if not self._container.can_connect():
            logger.debug("Could not connect to Nginx container")
            return False

        try:
            current_config = self._container.pull(self.config_path).read()
        except (ProtocolError, PathError) as e:
            logger.warning(
                "Could not check the current nginx configuration due to "
                "a failure in retrieving the file: %s",
                e,
            )
            return False

        return current_config != new_config

    def reload(self) -> None:
        """Reload the nginx config without restarting the service."""
        self._container.exec(["nginx", "-s", "reload"])

    @property
    def layer(self) -> Layer:
        """Return the Pebble layer for Nginx."""
        return Layer(
            {
                "summary": "nginx layer",
                "description": "pebble config layer for Nginx",
                "services": {
                    "nginx": {
                        "override": "replace",
                        "summary": "nginx",
                        "command": "nginx -g 'daemon off;'",
                        "startup": "enabled",
                    }
                },
            }
        )

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
        # TODO this code might be unnecessarily complex. Can we simplify it?
        if TempoRole.distributor in addresses_by_role.keys():
            addresses_mapped_to_upstreams["distributor"] = addresses_by_role[TempoRole.distributor]
        if TempoRole.query_frontend in addresses_by_role.keys():
            addresses_mapped_to_upstreams["query_frontend"] = addresses_by_role[
                TempoRole.query_frontend
            ]
        if TempoRole.all in addresses_by_role.keys():
            # for all, we add addresses to existing upstreams for distributor / query_frontend or create the set
            if "distributor" in addresses_mapped_to_upstreams:
                addresses_mapped_to_upstreams["distributor"] = addresses_mapped_to_upstreams[
                    "distributor"
                ].union(addresses_by_role[TempoRole.all])
            else:
                addresses_mapped_to_upstreams["distributor"] = addresses_by_role[TempoRole.all]
            if "query_frontend" in addresses_mapped_to_upstreams:
                addresses_mapped_to_upstreams["query_frontend"] = addresses_mapped_to_upstreams[
                    "query_frontend"
                ].union(addresses_by_role[TempoRole.all])
            else:
                addresses_mapped_to_upstreams["query_frontend"] = addresses_by_role[TempoRole.all]
        if "distributor" in addresses_mapped_to_upstreams.keys():
            nginx_upstreams.extend(
                self._distributor_upstreams(addresses_mapped_to_upstreams["distributor"])
            )
        if "query_frontend" in addresses_mapped_to_upstreams.keys():
            nginx_upstreams.extend(
                self._query_frontend_upstreams(addresses_mapped_to_upstreams["query_frontend"])
            )

        return nginx_upstreams

    def _distributor_upstreams(self, address_set):
        return [
            self._upstream("otlp-http", address_set, Tempo.receiver_ports["otlp_http"]),
            self._upstream("otlp-grpc", address_set, Tempo.receiver_ports["otlp_grpc"]),
            self._upstream("zipkin", address_set, Tempo.receiver_ports["zipkin"]),
            self._upstream(
                "jaeger-thrift-http", address_set, Tempo.receiver_ports["jaeger_thrift_http"]
            ),
        ]

    def _query_frontend_upstreams(self, address_set):
        return [
            self._upstream("tempo-http", address_set, Tempo.server_ports["tempo_http"]),
            self._upstream("tempo-grpc", address_set, Tempo.server_ports["tempo_grpc"]),
        ]

    def _upstream(self, role, address_set, port):
        return {
            "directive": "upstream",
            "args": [role],
            "block": [{"directive": "server", "args": [f"{addr}:{port}"]} for addr in address_set],
        }

    def _locations(self, upstream: str, grpc: bool) -> List[Dict[str, Any]]:
        protocol = "grpc" if grpc else "http"
        nginx_locations = [
            {
                "directive": "location",
                "args": ["/"],
                "block": [
                    {
                        "directive": "grpc_pass" if grpc else "proxy_pass",
                        "args": [f"{protocol}://{upstream}"],
                    }
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

    def _listen(self, port, ssl, http2):
        directives = []
        directives.append(
            {"directive": "listen", "args": self._listen_args(port, False, ssl, http2)}
        )
        directives.append(
            {"directive": "listen", "args": self._listen_args(port, True, ssl, http2)}
        )
        return directives

    def _listen_args(self, port, ipv6, ssl, http2):
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

    def _servers(
        self, addresses_by_role: Dict[str, Set[str]], tls: bool = False
    ) -> List[Dict[str, Any]]:
        servers = []
        roles = addresses_by_role.keys()

        if "distributor" in roles or "all" in roles:
            servers.append(
                self._server(Tempo.receiver_ports["otlp_http"], "otlp-http", False, tls)
            )
            servers.append(self._server(Tempo.receiver_ports["zipkin"], "zipkin", False, tls))
            servers.append(
                self._server(
                    Tempo.receiver_ports["jaeger_thrift_http"], "jaeger-thrift-http", False, tls
                )
            )
            servers.append(self._server(Tempo.receiver_ports["otlp_grpc"], "otlp-grpc", True, tls))
        if "query-frontend" in roles or "all" in roles:
            servers.append(
                self._server(Tempo.server_ports["tempo_http"], "tempo-http", False, tls)
            )
            servers.append(self._server(Tempo.server_ports["tempo_grpc"], "tempo-grpc", True, tls))
        return servers

    def _server(
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
                    *self._locations(upstream, grpc),
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
                *self._locations(upstream, grpc),
            ],
        }
