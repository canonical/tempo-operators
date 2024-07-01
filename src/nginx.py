# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

import logging
from typing import Any, Dict, List, Optional, Set

import crossplane
from ops import CharmBase
from ops.pebble import Layer

from tempo import Tempo
from tempo_cluster import TempoClusterProvider, TempoRole

logger = logging.getLogger(__name__)


NGINX_DIR = "/etc/nginx"
NGINX_CONFIG = f"{NGINX_DIR}/nginx.conf"
KEY_PATH = f"{NGINX_DIR}/certs/server.key"
CERT_PATH = f"{NGINX_DIR}/certs/server.cert"
CA_CERT_PATH = f"{NGINX_DIR}/certs/ca.cert"

LOCATIONS_DISTRIBUTOR: List[Dict[str, Any]] = [
    {
        "directive": "location",
        "args": ["/distributor"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://distributor"],
            },
        ],
    },
    # OTLP/HTTP ingestion
    {
        "directive": "location",
        "args": ["/v1/traces"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://otlp-http"],
            },
        ],
    },
    # Zipkin ingestion
    {
        "directive": "location",
        "args": ["/api/v2/spans"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://zipkin"],
            },
        ],
    },
    # # Jaeger thrift HTTP ingestion
    # {
    #     "directive": "location",
    #     "args": ["/api/traces"],
    #     "block": [
    #         {
    #             "directive": "proxy_pass",
    #             "args": ["http://jaeger-thrift-http"],
    #         },
    #     ],
    # },
]
# TODO add GRPC locations - perhaps as a separate server section?
LOCATIONS_QUERY_FRONTEND: List[Dict] = [
    {
        "directive": "location",
        "args": ["/prometheus"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/api/echo"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/api/traces"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/api/search"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/api/v2/search"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
    {
        "directive": "location",
        "args": ["/api/overrides"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
    # Buildinfo endpoint can go to any component
    {
        "directive": "location",
        "args": ["=", "/api/status/buildinfo"],
        "block": [
            {
                "directive": "proxy_pass",
                "args": ["http://query-frontend"],
            },
        ],
    },
]

LOCATIONS_BASIC: List[Dict] = [
    {
        "directive": "location",
        "args": ["=", "/"],
        "block": [
            {"directive": "return", "args": ["200", "'OK'"]},
            {"directive": "auth_basic", "args": ["off"]},
        ],
    },
    {  # Location to be used by nginx-prometheus-exporter
        "directive": "location",
        "args": ["=", "/status"],
        "block": [
            {"directive": "stub_status", "args": []},
        ],
    },
]


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
        if self._container.can_connect():
            self._container.push(
                self.config_path, self.config(tls=tls), make_dirs=True  # type: ignore
            )
            self._container.add_layer("nginx", self.layer, combine=True)
            self._container.autostart()

    def config(self, tls: bool = False) -> str:
        """Build and return the Nginx configuration."""
        full_config = self._prepare_config(tls)
        return crossplane.build(full_config)

    def _prepare_config(self, tls: bool = False) -> List[dict]:
        # TODO remember to put it back to error
        log_level = "debug"
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
                    self._server(addresses_by_role, tls),
                ],
            },
        ]
        return full_config

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
        if TempoRole.distributor in addresses_by_role.keys():
            addresses_mapped_to_upstreams["distributor"] = addresses_by_role[TempoRole.distributor]
        if TempoRole.query_frontend in addresses_by_role.keys():
            addresses_mapped_to_upstreams["query_frontend"] = addresses_by_role[TempoRole.query_frontend]
        if TempoRole.all in addresses_by_role.keys():
            # for all, we add addresses to existing upstreams for distributor / query_frontend or create the set
            if "distributor" in addresses_mapped_to_upstreams:
                addresses_mapped_to_upstreams["distributor"] = addresses_mapped_to_upstreams["distributor"].union(addresses_by_role[TempoRole.all])
            else:
                addresses_mapped_to_upstreams["distributor"] = addresses_by_role[TempoRole.all]
            if "query_frontend" in addresses_mapped_to_upstreams:
                addresses_mapped_to_upstreams["query_frontend"] = addresses_mapped_to_upstreams["query_frontend"].union(addresses_by_role[TempoRole.all])
            else:
                addresses_mapped_to_upstreams["query_frontend"] = addresses_by_role[TempoRole.all]
        if "distributor" in addresses_mapped_to_upstreams.keys():
            nginx_upstreams.extend(self._distributor_upstreams(addresses_mapped_to_upstreams["distributor"]))
        if "query_frontend" in addresses_mapped_to_upstreams.keys():
            nginx_upstreams.extend(self._query_frontend_upstreams(addresses_mapped_to_upstreams["query_frontend"]))

        return nginx_upstreams

    def _distributor_upstreams(self, address_set):
        return [
            self._upstream("distributor", address_set, Tempo.server_ports["tempo_http"]),
            self._upstream("otlp-http", address_set, Tempo.receiver_ports["otlp_http"]),
            self._upstream("zipkin", address_set, Tempo.receiver_ports["zipkin"]),
            self._upstream("jaeger-thrift-http", address_set, Tempo.receiver_ports["jaeger_thrift_http"]),
        ]

    def _query_frontend_upstreams(self, address_set):
        return [
            self._upstream("query-frontend", address_set, Tempo.server_ports["tempo_http"])
        ]

    def _upstream(self, role, address_set, port):
        return {
            "directive": "upstream",
            "args": [role],
            "block": [{"directive": "server", "args": [f"{addr}:{port}"]} for addr in address_set],
        }

    def _locations(self, addresses_by_role: Dict[str, Set[str]]) -> List[Dict[str, Any]]:
        nginx_locations = LOCATIONS_BASIC.copy()
        roles = addresses_by_role.keys()

        if "distributor" in roles or "all" in roles:
            # TODO split locations for every port
            nginx_locations.extend(LOCATIONS_DISTRIBUTOR)
        if "query-frontend" in roles or "all" in roles:
            nginx_locations.extend(LOCATIONS_QUERY_FRONTEND)
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

    def _listen(self, ssl):
        directives = []
        for port in Tempo.all_ports.values():
            directives.append({"directive": "listen", "args": [f"{port}", "ssl"] if ssl else [f"{port}"]})
            directives.append({"directive": "listen", "args": [f"[::]:{port}", "ssl"] if ssl else [f"[::]:{port}"]})
            return directives
        return directives

    def _server(self, addresses_by_role: Dict[str, Set[str]], tls: bool = False) -> Dict[str, Any]:
        auth_enabled = False

        if tls:
            return {
                "directive": "server",
                "args": [],
                "block": [
                    *self._listen(ssl=True),
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
                    *self._locations(addresses_by_role),
                ],
            }

        return {
            "directive": "server",
            "args": [],
            "block": [
                *self._listen(ssl=False),
                *self._basic_auth(auth_enabled),
                {
                    "directive": "proxy_set_header",
                    "args": ["X-Scope-OrgID", "$ensured_x_scope_orgid"],
                },
                # {
                #     "directive": "proxy_set_header",
                #     "args": ["Host", "$host:$server_port"]
                # },
                {"directive": "server_name", "args": [self.server_name]},
                *self._locations(addresses_by_role),
            ],
        }
