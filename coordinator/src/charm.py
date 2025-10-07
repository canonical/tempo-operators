#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Operator for Tempo; a lightweight object storage based tracing backend."""

import json
import logging
import re
import socket
from pathlib import Path
from subprocess import CalledProcessError, getoutput
from typing import Any, Dict, List, Optional, Set, Tuple, cast, get_args

import ops
import ops_tracing

# wokeignore:rule=blackbox
from charms.blackbox_exporter_k8s.v0.blackbox_probes import (
    BlackboxProbesProvider,  # wokeignore:rule=blackbox
)
from charms.catalogue_k8s.v1.catalogue import CatalogueItem
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.prometheus_k8s.v1.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from charms.tempo_coordinator_k8s.v0.tempo_api import (
    DEFAULT_RELATION_NAME as tempo_api_relation_name,
)
from charms.tempo_coordinator_k8s.v0.tempo_api import (
    TempoApiProvider,
)
from charms.tempo_coordinator_k8s.v0.tracing import (
    ReceiverProtocol,
    TracingEndpointProvider,
    TransportProtocolType,
    receiver_protocol_to_transport_protocol,
)
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from coordinated_workers.coordinator import Coordinator
from coordinated_workers.nginx import (
    CA_CERT_PATH,
    CERT_PATH,
    KEY_PATH,
    NginxConfig,
)
from coordinated_workers.telemetry_correlation import TelemetryCorrelation
from cosl.interfaces.datasource_exchange import DatasourceDict
from cosl.interfaces.utils import DatabagModel
from ops import CollectStatusEvent
from ops.charm import CharmBase

from tempo import Tempo
from tempo_config import TEMPO_ROLES_CONFIG, TempoRole
from nginx_config import upstreams, server_ports_to_locations
from cosl.reconciler import all_events, observe_events

logger = logging.getLogger(__name__)
PEERS_RELATION_ENDPOINT_NAME = "peers"
PROMETHEUS_DS_TYPE = "prometheus"
LOKI_DS_TYPE = "loki"


class TempoCoordinator(Coordinator):
    """A Tempo coordinator class that inherits from the Coordinator class."""

    @property
    def _charm_tracing_receivers_urls(self) -> Dict[str, str]:
        """Override with custom enabled and requested receivers."""
        # if related to a remote instance, return the remote instance's endpoints
        if self.charm_tracing.is_ready():
            return super()._charm_tracing_receivers_urls
        # return this instance's endpoints
        return self._charm.requested_receivers_urls()  # type: ignore

    @property
    def _workload_tracing_receivers_urls(self) -> Dict[str, str]:
        """Override with custom enabled and requested receivers."""
        # if related to a remote instance, return the remote instance's endpoints
        if self.workload_tracing.is_ready():
            return super()._workload_tracing_receivers_urls
        # return this instance's endpoints
        return self._charm.requested_receivers_urls()  # type: ignore

    def _setup_charm_tracing(self):
        """Override regular charm tracing setup because we're likely sending the traces to ourselves."""
        # if we have an external endpoint, use it; so far, this is the superclass behaviour
        endpoint = (
            self.charm_tracing.get_endpoint("otlp_http")
            if self.charm_tracing.is_ready()
            else None
        )
        # else, we send to localhost
        tls_config = self.tls_config
        endpoint = (
            endpoint
            or f"http{'s' if tls_config else ''}://localhost:{Tempo.otlp_http_receiver_port}"
        )

        url = endpoint + "/v1/traces"
        ops_tracing.set_destination(
            url=url,
            ca=tls_config.ca_cert if tls_config else None,
        )


class PeerData(DatabagModel):
    """Databag model for the "peers" relation between coordinator units."""

    fqdn: str
    """FQDN hostname of this coordinator unit."""


class TempoCoordinatorCharm(CharmBase):
    """Charmed Operator for Tempo; a distributed tracing backend."""

    def __init__(self, *args):
        super().__init__(*args)

        # INTEGRATIONS
        self.ingress = TraefikRouteRequirer(
            self,
            self.model.get_relation("ingress"),  # type: ignore
            "ingress",
        )
        self.tracing = TracingEndpointProvider(
            self, external_url=self._most_external_url
        )
        # set alert_rules_path="", as we don't want to populate alert rules into the relation databag
        # we only need `self._remote_write.endpoints`
        self._remote_write = PrometheusRemoteWriteConsumer(self, alert_rules_path="")

        self.tempo = Tempo(
            retention_period_hours=self._trace_retention_period_hours,
            remote_write_endpoints=self._remote_write_endpoints,
        )

        # keep this above the coordinator definition
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

        # needs self.tempo and self.tracing attributes to be set
        # do it once on init for efficiency; instead of calculating the same set multiple
        # times throughout an event
        self._requested_receivers = self._collect_requested_receivers()
        requested_receiver_ports: Dict[ReceiverProtocol, int] = {
            proto: Tempo.receiver_ports[proto] for proto in self._requested_receivers
        }
        self.coordinator = TempoCoordinator(
            charm=self,
            roles_config=TEMPO_ROLES_CONFIG,
            external_url=self._most_external_url,
            worker_metrics_port=self.tempo.tempo_http_server_port,
            endpoints={
                "certificates": "certificates",
                "cluster": "tempo-cluster",
                "grafana-dashboards": "grafana-dashboard",
                "logging": "logging",
                "metrics": "metrics-endpoint",
                "s3": "s3",
                "charm-tracing": "self-charm-tracing",
                "workload-tracing": "self-workload-tracing",
                "send-datasource": None,
                "receive-datasource": "receive-datasource",
                "catalogue": "catalogue",
            },
            nginx_config=NginxConfig(
                server_name=self.hostname,
                upstream_configs=upstreams(
                    requested_receiver_ports=requested_receiver_ports
                ),
                server_ports_to_locations=server_ports_to_locations(
                    requested_receiver_ports=requested_receiver_ports
                ),
            ),
            workers_config=self.tempo.config,
            # set the resource request for the nginx container
            resources_requests=self.get_resources_requests,
            container_name="nginx",
            remote_write_endpoints=self._remote_write_endpoints,  # type: ignore
            worker_ports=self._get_worker_ports,
            workload_tracing_protocols=["otlp_http"],
            catalogue_item=self._catalogue_item,
        )

        self._telemetry_correlation = TelemetryCorrelation(self, grafana_ds_endpoint="grafana-source", grafana_dsx_endpoint="receive-datasource")

        # configure this tempo as a datasource in grafana
        self.grafana_source_provider = GrafanaSourceProvider(
            self,
            source_type="tempo",
            source_url=self._external_http_server_url,
            extra_fields=self._build_grafana_source_extra_fields(),
            is_ingress_per_app=self._is_ingress_ready,
        )

        # wokeignore:rule=blackbox
        self.probes_provider = BlackboxProbesProvider(
            self,
            probes=[
                {
                    "params": {"module": ["http_2xx"]},
                    "static_configs": [
                        {"targets": [self._external_http_server_url + "/ready"]}
                    ],
                }
            ],
        )

        # OBSERVERS
        # peer
        self.framework.observe(
            self.on[PEERS_RELATION_ENDPOINT_NAME].relation_created,
            self._on_peers_relation_created,
        )

        self.tempo_api = TempoApiProvider(
            relation_mapping=self.model.relations,
            relation_name=tempo_api_relation_name,
            app=self.app,
        )

        # refuse to handle any other event as we can't possibly know what to do.
        if not self.coordinator.can_handle_events:
            # logging is handled by the Coordinator object
            return

        # actions
        self.framework.observe(
            self.on.list_receivers_action, self._on_list_receivers_action
        )

        # do this regardless of what event we are processing
        observe_events(self, all_events, self._reconcile)

    ######################
    # UTILITY PROPERTIES #
    ######################
    @property
    def peers(self):
        """Fetch the "peers" peer relation."""
        return self.model.get_relation(PEERS_RELATION_ENDPOINT_NAME)

    @property
    def _external_hostname(self) -> str:
        """Return the external hostname."""
        return re.sub(r"^https?:\/\/", "", self._most_external_url)

    @property
    def hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def app_hostname(self) -> str:
        """Application-level fqdn."""
        return Coordinator.app_hostname(
            hostname=self.hostname, app_name=self.app.name, model_name=self.model.name
        )

    @property
    def _is_ingress_ready(self) -> bool:
        "Return True if an ingress is configured and ready, otherwise False."
        return bool(
            self.ingress.is_ready()
            and self.ingress.scheme
            and self.ingress.external_host
        )

    @property
    def _external_http_server_url(self) -> str:
        """External url of the http(s) server."""
        return f"{self._most_external_url}:{Tempo.tempo_http_server_port}"

    @property
    def _external_url(self) -> Optional[str]:
        """Return the external URL if the ingress is configured and ready, otherwise None."""
        if self._is_ingress_ready:
            ingress_url = f"{self.ingress.scheme}://{self.ingress.external_host}"
            logger.debug("This unit's ingress URL: %s", ingress_url)
            return ingress_url

        return None

    @property
    def _most_external_url(self) -> str:
        """Return the most external url known about by this charm.

        This will return the first of:
        - the external URL, if the ingress is configured and ready
        - the internal URL
        """
        external_url = self._external_url
        if external_url:
            return external_url
        # If we do not have an ingress, then use the K8s service.
        return self._internal_url

    @property
    def _scheme(self) -> str:
        """Return the URI scheme that should be used when communicating with this unit."""
        scheme = "http"
        if self.are_certificates_on_disk:
            scheme = "https"
        return scheme

    @property
    def _internal_url(self) -> str:
        """Return the locally addressable, FQDN based service address."""
        return f"{self._scheme}://{self.app_hostname}"

    @property
    def are_certificates_on_disk(self) -> bool:
        """Return True if the certificates files are on disk."""
        nginx_container = self.unit.get_container("nginx")

        return (
            nginx_container.can_connect()
            and nginx_container.exists(CERT_PATH)
            and nginx_container.exists(KEY_PATH)
            and nginx_container.exists(CA_CERT_PATH)
        )

    @property
    def enabled_receivers(self) -> Set[str]:
        """Extra receivers enabled through config"""
        enabled_receivers = set()
        # otlp_http is needed by charm_tracing
        enabled_receivers.add("otlp_http")
        enabled_receivers.update(
            [
                receiver
                for receiver in get_args(ReceiverProtocol)
                if self.config.get(f"always_enable_{receiver}") is True
            ]
        )
        return enabled_receivers

    @property
    def _catalogue_item(self) -> CatalogueItem:
        port = 3200
        api_endpoints = {
            "Search traces": "/api/search?<params>",
            "Query traces (by ID)": "/api/traces/<traceID>",
            "Status": "/status",
        }
        """A catalogue application entry for this Tempo instance."""
        return CatalogueItem(
            # use app.name in case there are multiple Tempo applications deployed.
            name=f"Tempo ({self.app.name})",
            icon="transit-connection-variant",
            # Since Tempo doesn't have a UI (unlike Prometheus), we will leave the URL field empty.
            url="",
            description=(
                "Tempo is a distributed tracing backend by Grafana, supporting Jaeger, "
                "Zipkin, and OpenTelemetry protocols."
            ),
            api_docs="https://grafana.com/docs/tempo/latest/api_docs/",
            api_endpoints={
                key: f"{self._most_external_url}:{port}{path}"
                for key, path in api_endpoints.items()
            },
        )

    ##################
    # EVENT HANDLERS #
    ##################
    def _on_peers_relation_created(self, _: ops.RelationCreatedEvent):
        self.update_peer_data()

    def _on_list_receivers_action(self, event: ops.ActionEvent):
        res = {}
        for receiver in self._requested_receivers:
            res[receiver.replace("_", "-")] = self.get_receiver_url(receiver)
        event.set_results(res)

    def _on_collect_status(self, e: CollectStatusEvent):
        # add Tempo coordinator-specific statuses
        if (
            "metrics-generator" in self.coordinator.cluster.gather_roles()
            and not self._remote_write_endpoints()
        ):
            e.add_status(
                ops.ActiveStatus(
                    "metrics-generator disabled. Add a relation over send-remote-write"
                )
            )

    ###################
    # UTILITY METHODS #
    ###################

    def update_peer_data(self) -> None:
        """Update peer unit data bucket with this unit's hostname."""
        if self.peers and self.peers.data:
            PeerData(fqdn=self.hostname).dump(self.peers.data[self.unit])

    def get_peer_data(self, unit: ops.Unit) -> Optional[PeerData]:
        """Get peer data from a given unit data bucket."""
        if not (self.peers and self.peers.data):
            return None

        return PeerData.load(self.peers.data.get(unit, {}))

    def _update_ingress_relation(self) -> None:
        """Make sure the traefik route is up-to-date."""
        if not self.unit.is_leader():
            return

        if self.ingress.is_ready():
            self.ingress.submit_to_traefik(
                self._ingress_config, static=self._static_ingress_config
            )

    def _update_tempo_api_relations(self) -> None:
        """Update all applications related to us via the tempo-api relation."""
        if not self.unit.is_leader():
            return

        # tempo-api should only send an external URL if it's set, otherwise it leaves that empty
        internal_url = self._internal_url
        direct_url_http = internal_url + f":{self.tempo.server_ports['tempo_http']}"
        direct_url_grpc = internal_url + f":{self.tempo.server_ports['tempo_grpc']}"

        external_url = self._external_url
        if external_url:
            ingress_url_http = (
                external_url + f":{self.tempo.server_ports['tempo_http']}"
            )
            ingress_url_grpc = (
                external_url + f":{self.tempo.server_ports['tempo_grpc']}"
            )
        else:
            ingress_url_http = None
            ingress_url_grpc = None

        self.tempo_api.publish(
            direct_url_http=direct_url_http,
            direct_url_grpc=direct_url_grpc,
            ingress_url_http=ingress_url_http,
            ingress_url_grpc=ingress_url_grpc,
        )

    def _update_tracing_relations(self) -> None:
        tracing_relations = self.model.relations["tracing"]
        if not tracing_relations:
            # todo: set waiting status and configure tempo to run without receivers if possible,
            #  else perhaps postpone starting the workload at all.
            logger.warning("no tracing relations: Tempo has no receivers configured.")
            return

        requested_receivers = self._requested_receivers
        # publish requested protocols to all relations
        if self.unit.is_leader():
            self.tracing.publish_receivers(
                [(p, self.get_receiver_url(p)) for p in requested_receivers]
            )

    def _collect_requested_receivers(self) -> Tuple[ReceiverProtocol, ...]:
        """List what receivers we should activate, based on the active tracing relations and config-enabled extra receivers."""
        # we start with the sum of the requested endpoints from the requirers
        requested_protocols = set(self.tracing.requested_protocols())

        # update with enabled extra receivers
        requested_protocols.update(self.enabled_receivers)
        # and publish only those we support
        requested_receivers = requested_protocols.intersection(
            set(self.tempo.receiver_ports)
        )
        # sorting for stable output to prevent remote units from receiving
        # spurious relation-changed events
        return tuple(sorted(requested_receivers))

    @property
    def _trace_retention_period_hours(self) -> int:
        """Trace retention period for the compactor."""
        # if unset, defaults to 30 days
        return cast(int, self.config["retention-period"])

    def server_ca_cert(self) -> Optional[str]:
        """For charm tracing."""
        return CA_CERT_PATH if Path(CA_CERT_PATH).exists() else None

    def tempo_otlp_http_endpoint(self) -> Optional[str]:
        """Endpoint at which the charm tracing information will be forwarded."""
        # the charm container and the tempo workload container have apparently the same
        # IP, so we can talk to tempo at localhost.
        if hasattr(self, "coordinator") and self.coordinator.charm_tracing.is_ready():
            return self.coordinator.charm_tracing.get_endpoint("otlp_http")
        # In absence of another Tempo instance, we don't want to lose this instance's charm traces
        elif self.is_workload_ready():
            return f"{self._internal_url}:{self.tempo.receiver_ports['otlp_http']}"

        return None

    def requested_receivers_urls(self) -> Dict[str, str]:
        """Endpoints to which the workload (and the worker charm) can push traces to."""
        return {
            receiver: self.get_receiver_url(receiver)
            for receiver in self._requested_receivers
        }

    @property
    def _static_ingress_config(self) -> dict:
        entry_points = {}
        for protocol, port in self.tempo.all_ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            entry_points[sanitized_protocol] = {"address": f":{port}"}

        return {"entryPoints": entry_points}

    @property
    def _ingress_config(self) -> dict:
        """Build a raw ingress configuration for Traefik."""
        http_routers = {}
        http_services = {}
        middlewares = {}
        for protocol, port in self.tempo.all_ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            redirect_middleware = (
                {
                    f"juju-{self.model.name}-{self.model.app.name}-middleware-{sanitized_protocol}": {
                        "redirectScheme": {
                            "permanent": True,
                            "port": port,
                            "scheme": "https",
                        }
                    }
                }
                if self.ingress.is_ready() and self.ingress.scheme == "https"
                else {}
            )
            middlewares.update(redirect_middleware)

            http_routers[
                f"juju-{self.model.name}-{self.model.app.name}-{sanitized_protocol}"
            ] = {
                "entryPoints": [sanitized_protocol],
                "service": f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}",
                # TODO better matcher
                "rule": "ClientIP(`0.0.0.0/0`)",
                **(
                    {"middlewares": list(redirect_middleware.keys())}
                    if redirect_middleware
                    else {}
                ),
            }
            if (
                protocol == "tempo_grpc"
                or receiver_protocol_to_transport_protocol.get(
                    cast(ReceiverProtocol, protocol)
                )
                == TransportProtocolType.grpc
            ) and not self.coordinator.tls_available:
                # to send traces to unsecured GRPC endpoints, we need h2c
                # see https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-http-h2c
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {
                    "loadBalancer": {
                        "servers": self._build_lb_server_config("h2c", port)
                    }
                }
            else:
                # anything else, including secured GRPC, can use _internal_url
                # ref https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-https
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {
                    "loadBalancer": {
                        "servers": self._build_lb_server_config(self._scheme, port)
                    }
                }

        return {
            "http": {
                "routers": http_routers,
                "services": http_services,
                # else we get: level=error msg="Error occurred during watcher callback:
                # ...: middlewares cannot be a standalone element (type map[string]*dynamic.Middleware)"
                # providerName=file
                **({"middlewares": middlewares} if middlewares else {}),
            }
        }

    def _build_lb_server_config(self, scheme: str, port: int) -> List[Dict[str, str]]:
        """build the server portion of the loadbalancer config of Traefik ingress."""

        def to_url(fqdn: str):
            return {"url": f"{scheme}://{fqdn}:{port}"}

        urls = [to_url(self.hostname)]
        if self.peers:
            for peer in self.peers.units:
                peer_data = self.get_peer_data(peer)
                if peer_data:
                    urls.append(to_url(peer_data.fqdn))

        return urls

    def get_receiver_url(self, protocol: ReceiverProtocol):
        """Return the receiver endpoint URL based on the protocol.

        if ingress is used, return endpoint provided by the ingress instead.
        """
        protocol_type = receiver_protocol_to_transport_protocol.get(protocol)
        receiver_port = self.tempo.receiver_ports[protocol]

        if self._is_ingress_ready:
            url = (
                self.ingress.external_host
                if protocol_type == TransportProtocolType.grpc
                else f"{self.ingress.scheme}://{self.ingress.external_host}"
            )
        else:
            url = (
                self.app_hostname
                if protocol_type == TransportProtocolType.grpc
                else self._internal_url
            )

        return f"{url}:{receiver_port}"

    def is_workload_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        if self.coordinator.tls_available:
            tls, s = f" --cacert {CA_CERT_PATH}", "s"
        else:
            tls = s = ""

        # cert is for fqdn/ingress, not for IP
        cmd = f"curl{tls} http{s}://{self.coordinator.hostname}:{Tempo.tempo_http_server_port}/ready"

        try:
            out = getoutput(cmd).split("\n")[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == "ready"

    def get_resources_requests(self, _) -> Dict[str, str]:
        """Returns a dictionary for the "requests" portion of the resources requirements."""
        return {"cpu": "50m", "memory": "100Mi"}

    def _remote_write_endpoints(self) -> List[Dict[str, str]]:
        """Return a sorted list of remote-write endpoints."""
        return sorted(self._remote_write.endpoints, key=lambda x: x["url"])

    def _update_source_exchange(self) -> None:
        """Update the grafana-datasource-exchange relations with what we receive from grafana-source."""

        # leader operation only
        if not self.unit.is_leader():
            return

        # Each grafana we're sending our data to gives us back a mapping from unit names to datasource UIDs.
        #   so if we have two tempo units, and we're related to two grafanas,
        #   the grafana-source databag will look something like this:
        # {"grafana_uid": "1234-1234-1234-1234",
        # "datasource_uids": {
        #     "grafana/0": {
        #         "tempo/0": "0000-0000-0000-0000",
        #         "tempo/1": "0000-0000-0000-0001"
        #         },
        #     "grafana/1": {
        #         "tempo/0": "0000-0000-0000-0000",
        #         "tempo/1": "0000-0000-0000-0001"
        #     },
        # }}
        # This is an implementation detail, but the UID is 'unique-per-grafana' and ATM it turns out to be
        #   deterministic given tempo's jujutopology, so if we are related to multiple grafanas,
        #   the UIDs they assign to our units will be the same.
        # Either way, we assume for simplicity (also, why not) that we're sending the same data to
        #   different grafanas, and not somehow different subsets to different places. Therefore, it should not
        #   matter which grafana you talk to, to cross-reference the data this unit is presenting to the world.
        # However, if at some point grafana's algorithm to generate source UIDs changes, we'd be in trouble since
        #   the unit we are sharing our data to might be talking to 'another grafana' which might be using a
        #   different UID convention!

        # we might have multiple grafana-source relations, this method collects them all and returns a mapping from
        # the `grafana_uid` to the contents of the `datasource_uids` field
        grafana_uids_to_units_to_uids = self.grafana_source_provider.get_source_uids()
        raw_datasources: List[DatasourceDict] = []

        for grafana_uid, ds_uids in grafana_uids_to_units_to_uids.items():
            # we don't need the grafana unit name
            for _, ds_uid in ds_uids.items():
                # we also don't care about which unit's server we're looking at, since hopefully the data is the same.
                raw_datasources.append(
                    {"type": "tempo", "uid": ds_uid, "grafana_uid": grafana_uid}
                )

        # publish() already sorts the data for us, to prevent databag flapping and ensuing event storms
        self.coordinator.datasource_exchange.publish(datasources=raw_datasources)

    def _update_grafana_source(self) -> None:
        """Update grafana-source relations."""
        self.grafana_source_provider.update_source(
            source_url=self._external_http_server_url
        )

    def _reconcile(self):
        # This method contains unconditional update logic, i.e. logic that should be executed
        # regardless of the event we are processing.
        # reason is, if we miss these events because our coordinator cannot process events (inconsistent status),
        # we need to 'remember' to run this logic as soon as we become ready, which is hard and error-prone
        self._update_ingress_relation()
        self._update_tracing_relations()
        self._update_source_exchange()
        # reconcile grafana-source databags to update `extra_fields`
        # if it gets changed by any other influencing relation.
        self._update_grafana_source()
        # open the necessary ports on this unit
        self.unit.set_ports(*self._nginx_ports)
        self._update_tempo_api_relations()

    def _get_grafana_source_uids(self) -> Dict[str, Dict[str, str]]:
        """Helper method to retrieve the databags of any grafana-source relations.

        Duplicate implementation of GrafanaSourceProvider.get_source_uids() to use in the
        situation where we want to access relation data when the GrafanaSourceProvider object
        is not yet initialised.
        """
        uids = {}
        for rel in self.model.relations.get("grafana-source", []):
            if not rel:
                continue
            app_databag = rel.data[rel.app]
            grafana_uid = app_databag.get("grafana_uid")
            if not grafana_uid:
                logger.warning(
                    "remote end is using an old grafana_datasource interface: "
                    "`grafana_uid` field not found."
                )
                continue

            uids[grafana_uid] = json.loads(app_databag.get("datasource_uids", "{}"))
        return uids

    def _build_service_graph_config(self) -> Dict[str, Any]:
        """Build the service graph config based on matching datasource UIDs.

        To enable service graphs, we need the datasource UID of any prometheus/mimir instance such that:
        1- Tempo is connected to it over "send-remote-write".
        2- It is also connected, over `grafana_datasource`, to at least one of the grafana instance(s) that Tempo is connected to.

        If there are multiple datasources that fit this description, we can assume that they are all
        equivalent and we can use any of them.
        """
        if datasource := self._telemetry_correlation.find_correlated_datasource(
            "send-remote-write",
            PROMETHEUS_DS_TYPE,
            "service graph",
        ):
            return {
                "serviceMap": {
                    "datasourceUid": datasource.uid,
                },
            }
        return {}

    def _build_traces_to_logs_config(self) -> Dict[str, Any]:
        if datasource := self._telemetry_correlation.find_correlated_datasource(
            "logging",
            LOKI_DS_TYPE,
            "traces-to-logs",
        ):
            return {
                "tracesToLogsV2": {
                    "datasourceUid": datasource.uid,
                    "tags": [
                        {"key": "juju_application", "value": ""},
                        {"key": "juju_model", "value": ""},
                        {"key": "juju_model_uuid", "value": ""},
                    ],
                    "filterByTraceID": False,
                    "filterBySpanID": False,
                    "customQuery": True,
                    "query": '{ $$__tags } |= "$${__span.traceId}"',
                }
            }

        return {}

    def _build_grafana_source_extra_fields(self) -> Optional[Dict[str, Any]]:
        """Extra fields needed for the grafana-source relation, like data correlation config."""
        ## https://grafana.com/docs/tempo/latest/metrics-generator/service_graphs/enable-service-graphs/
        # "httpMethod": "GET",
        # "serviceMap": {
        #     "datasourceUid": "juju_svcgraph_61e32e2f-50ac-40e7-8ee8-1b7297a3e47f_prometheus_0",
        # },
        # # https://community.grafana.com/t/how-to-jump-from-traces-to-logs/72477/3
        # "tracesToLogs": {
        #     "datasourceUid": "juju_svcgraph_61e32e2f-50ac-40e7-8ee8-1b7297a3e47f_loki_0"
        # },
        # "lokiSearch": {
        #     "datasourceUid": "juju_svcgraph_61e32e2f-50ac-40e7-8ee8-1b7297a3e47f_loki_0"
        # },

        svc_graph_config = self._build_service_graph_config()

        traces_to_logs_config = self._build_traces_to_logs_config()

        if not svc_graph_config and not traces_to_logs_config:
            return None

        return {
            "httpMethod": "GET",
            **svc_graph_config,
            **traces_to_logs_config,
        }

    @property
    def _nginx_ports(self) -> Tuple[int, ...]:
        """The ports that we should open on this pod."""
        # we only open the ports that we need to open: that is, the worker ports for all roles.
        return self._get_worker_ports("all")

    def _get_worker_ports(self, role: str) -> Tuple[int, ...]:
        """Determine, from the role of a worker, which ports it should open."""
        # some ports that all workers should open
        ports: Set[int] = {
            Tempo.memberlist_port,
            # we need this because the metrics server is on the same port
            # technically we don't need it the way things are set up right now, because prometheus
            # scrapes the worker units over their fqdn, which is an UNIT IP address (not APP), because
            # juju uses statefulsets and not deployments. We add it anyway to err on the safe side.
            Tempo.tempo_http_server_port,
        }

        tempo_role = TempoRole(role)

        # distributor nodes should be able to accept spans in all supported formats
        if tempo_role in {TempoRole.all, TempoRole.distributor}:
            for enabled_receiver in self._requested_receivers:
                ports.add(Tempo.receiver_ports[enabled_receiver])

        # open server ports in query_frontend role
        if tempo_role in {TempoRole.all, TempoRole.query_frontend}:
            ports.add(Tempo.tempo_grpc_server_port)
        return tuple(ports)


if __name__ == "__main__":  # pragma: nocover
    from ops import main

    main(TempoCoordinatorCharm)  # noqa
