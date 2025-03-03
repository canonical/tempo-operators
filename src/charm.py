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

# wokeignore:rule=blackbox
from charms.blackbox_exporter_k8s.v0.blackbox_probes import (
    BlackboxProbesProvider,  # wokeignore:rule=blackbox
)
from charms.catalogue_k8s.v1.catalogue import CatalogueItem
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.prometheus_k8s.v1.prometheus_remote_write import (
    PrometheusRemoteWriteConsumer,
)
from charms.tempo_coordinator_k8s.v0.charm_tracing import trace_charm
from charms.tempo_coordinator_k8s.v0.tracing import (
    ReceiverProtocol,
    TracingEndpointProvider,
    TransportProtocolType,
    receiver_protocol_to_transport_protocol,
)
from charms.traefik_k8s.v0.traefik_route import TraefikRouteRequirer
from cosl.coordinated_workers.coordinator import ClusterRolesConfig, Coordinator
from cosl.coordinated_workers.nginx import CA_CERT_PATH, CERT_PATH, KEY_PATH
from cosl.interfaces.datasource_exchange import DatasourceDict, DSExchangeAppData
from cosl.interfaces.utils import DatabagModel, DataValidationError
from ops import CollectStatusEvent
from ops.charm import CharmBase

from nginx_config import NginxConfig
from tempo import Tempo
from tempo_config import TEMPO_ROLES_CONFIG

logger = logging.getLogger(__name__)
PEERS_RELATION_ENDPOINT_NAME = "peers"
PROMETHEUS_DS_TYPE = "prometheus"


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


class PeerData(DatabagModel):
    """Databag model for the "peers" relation between coordinator units."""

    fqdn: str
    """FQDN hostname of this coordinator unit."""


@trace_charm(
    tracing_endpoint="tempo_otlp_http_endpoint",
    server_cert="server_ca_cert",
    extra_types=(
        Tempo,
        TracingEndpointProvider,
        Coordinator,
        ClusterRolesConfig,
    ),
    # use PVC path for buffer data, so we don't lose it on pod churn
    buffer_path=Path("/tempo-data/.charm_tracing_buffer.raw"),
)
class TempoCoordinatorCharm(CharmBase):
    """Charmed Operator for Tempo; a distributed tracing backend."""

    def __init__(self, *args):
        super().__init__(*args)

        self.ingress = TraefikRouteRequirer(self, self.model.get_relation("ingress"), "ingress")  # type: ignore
        self.tempo = Tempo(
            requested_receivers=self._requested_receivers,
            retention_period_hours=self._trace_retention_period_hours,
        )
        # set alert_rules_path="", as we don't want to populate alert rules into the relation databag
        # we only need `self._remote_write.endpoints`
        self._remote_write = PrometheusRemoteWriteConsumer(self, alert_rules_path="")
        # set the open ports for this unit
        self.unit.set_ports(*self.tempo.all_ports.values())

        self.tracing = TracingEndpointProvider(self, external_url=self._most_external_url)

        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

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
            nginx_config=NginxConfig(server_name=self.hostname).config,
            workers_config=self.tempo.config,
            resources_requests=self.get_resources_requests,
            container_name="charm",
            remote_write_endpoints=self.remote_write_endpoints,  # type: ignore
            workload_tracing_protocols=["otlp_http"],
            catalogue_item=self._catalogue_item,
        )

        # configure this tempo as a datasource in grafana
        self.grafana_source_provider = GrafanaSourceProvider(
            self,
            source_type="tempo",
            source_url=self._external_http_server_url,
            refresh_event=[
                # refresh the source url when TLS config might be changing
                self.on[self.coordinator.cert_handler.certificates_relation_name].relation_changed,
                # or when ingress changes
                self.ingress.on.ready,
            ],
            extra_fields=self._build_grafana_source_extra_fields(),
        )

        # wokeignore:rule=blackbox
        self.probes_provider = BlackboxProbesProvider(
            self,
            probes=[
                {
                    "params": {"module": ["http_2xx"]},
                    "static_configs": [{"targets": [self._external_http_server_url + "/ready"]}],
                }
            ],
        )

        # peer
        self.framework.observe(
            self.on[PEERS_RELATION_ENDPOINT_NAME].relation_created,
            self._on_peers_relation_created,
        )

        # refuse to handle any other event as we can't possibly know what to do.
        if not self.coordinator.can_handle_events:
            # logging is handled by the Coordinator object
            return

        # do this regardless of what event we are processing
        self._reconcile()

        # actions
        self.framework.observe(self.on.list_receivers_action, self._on_list_receivers_action)

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
    def _external_http_server_url(self) -> str:
        """External url of the http(s) server."""
        return f"{self._most_external_url}:{self.tempo.tempo_http_server_port}"

    @property
    def _external_url(self) -> Optional[str]:
        """Return the external URL if the ingress is configured and ready, otherwise None."""
        if self.ingress.is_ready() and self.ingress.scheme and self.ingress.external_host:
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
        # If we do not have an ingress, then use the pod hostname.
        # The reason to prefer this over the pod name (which is the actual
        # hostname visible from the pod) or a K8s service, is that those
        # are routable virtually exclusively inside the cluster (as they rely)
        # on the cluster's DNS service, while the ip address is _sometimes_
        # routable from the outside, e.g., when deploying on MicroK8s on Linux.
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
        """Return the locally addressable, FQDN based unit address."""
        return f"{self._scheme}://{self.hostname}"

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
        """A catalogue application entry for this Tempo instance."""
        return CatalogueItem(
            # use app.name in case there are multiple Tempo applications deployed.
            name=self.app.name,
            icon="transit-connection-variant",
            # Unlike Prometheus, Tempo doesn't have a sophisticated web UI.
            # Instead, we'll show the current cluster members and their health status.
            # ref: https://grafana.com/docs/tempo/latest/api_docs/
            url=f"{self._most_external_url}:3200/memberlist",
            description=(
                "Tempo is a distributed tracing backend by Grafana, supporting Jaeger, "
                "Zipkin, and OpenTelemetry protocols."
            ),
        )

    ##################
    # EVENT HANDLERS #
    ##################
    def _on_peers_relation_created(self, _: ops.RelationCreatedEvent):
        self.update_peer_data()

    def _on_list_receivers_action(self, event: ops.ActionEvent):
        res = {}
        for receiver in self._requested_receivers():
            res[receiver.replace("_", "-")] = self.get_receiver_url(receiver)
        event.set_results(res)

    def _on_collect_status(self, e: CollectStatusEvent):
        # add Tempo coordinator-specific statuses
        if (
            "metrics-generator" in self.coordinator.cluster.gather_roles()
            and not self.remote_write_endpoints()
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

    def _update_tracing_relations(self) -> None:
        tracing_relations = self.model.relations["tracing"]
        if not tracing_relations:
            # todo: set waiting status and configure tempo to run without receivers if possible,
            #  else perhaps postpone starting the workload at all.
            logger.warning("no tracing relations: Tempo has no receivers configured.")
            return

        requested_receivers = self._requested_receivers()
        # publish requested protocols to all relations
        if self.unit.is_leader():
            self.tracing.publish_receivers(
                [(p, self.get_receiver_url(p)) for p in requested_receivers]
            )

    def _requested_receivers(self) -> Tuple[ReceiverProtocol, ...]:
        """List what receivers we should activate, based on the active tracing relations and config-enabled extra receivers."""
        # we start with the sum of the requested endpoints from the requirers
        requested_protocols = set(self.tracing.requested_protocols())

        # update with enabled extra receivers
        requested_protocols.update(self.enabled_receivers)
        # and publish only those we support
        requested_receivers = requested_protocols.intersection(set(self.tempo.receiver_ports))
        # sorting for stable output to prevent remote units from receiving
        # spurious relation-changed events
        return tuple(sorted(requested_receivers))

    @property
    def _trace_retention_period_hours(self) -> int:
        """Trace retention period for the compactor."""
        # if unset, defaults to 30 days
        return cast(int, self.config["retention-period"])

    def server_ca_cert(self) -> str:
        """For charm tracing."""
        return CA_CERT_PATH

    def tempo_otlp_http_endpoint(self) -> Optional[str]:
        """Endpoint at which the charm tracing information will be forwarded."""
        # the charm container and the tempo workload container have apparently the same
        # IP, so we can talk to tempo at localhost.
        if hasattr(self, "coordinator") and self.coordinator.charm_tracing.is_ready():
            return self.coordinator.charm_tracing.get_endpoint("otlp_http")
        # In absence of another Tempo instance, we don't want to lose this instance's charm traces
        elif self.is_workload_ready():
            return f"{self._internal_url}:{self.tempo.receiver_ports['otlp_http']}"

    def requested_receivers_urls(self) -> Dict[str, str]:
        """Endpoints to which the workload (and the worker charm) can push traces to."""
        return {
            receiver: self.get_receiver_url(receiver) for receiver in self._requested_receivers()
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
        for protocol, port in self.tempo.all_ports.items():
            sanitized_protocol = protocol.replace("_", "-")
            http_routers[f"juju-{self.model.name}-{self.model.app.name}-{sanitized_protocol}"] = {
                "entryPoints": [sanitized_protocol],
                "service": f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}",
                # TODO better matcher
                "rule": "ClientIP(`0.0.0.0/0`)",
            }
            if (
                protocol == "tempo_grpc"
                or receiver_protocol_to_transport_protocol.get(cast(ReceiverProtocol, protocol))
                == TransportProtocolType.grpc
            ) and not self.coordinator.tls_available:
                # to send traces to unsecured GRPC endpoints, we need h2c
                # see https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-http-h2c
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {"loadBalancer": {"servers": self._build_lb_server_config("h2c", port)}}
            else:
                # anything else, including secured GRPC, can use _internal_url
                # ref https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-https
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {"loadBalancer": {"servers": self._build_lb_server_config(self._scheme, port)}}
        return {
            "http": {
                "routers": http_routers,
                "services": http_services,
            },
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
        # ingress.is_ready returns True even when traefik hasn't sent any data yet
        has_ingress = (
            self.ingress.is_ready() and self.ingress.external_host and self.ingress.scheme
        )
        receiver_port = self.tempo.receiver_ports[protocol]

        if has_ingress:
            url = (
                self.ingress.external_host
                if protocol_type == TransportProtocolType.grpc
                else f"{self.ingress.scheme}://{self.ingress.external_host}"
            )
        else:
            url = (
                self.hostname
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
        cmd = f"curl{tls} http{s}://{self.coordinator.hostname}:{self.tempo.tempo_http_server_port}/ready"

        try:
            out = getoutput(cmd).split("\n")[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == "ready"

    def get_resources_requests(self, _) -> Dict[str, str]:
        """Returns a dictionary for the "requests" portion of the resources requirements."""
        return {"cpu": "50m", "memory": "100Mi"}

    def remote_write_endpoints(self):
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
        self.grafana_source_provider.update_source(source_url=self._external_http_server_url)

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

        dsx_relations = {
            relation.app.name: relation
            for relation in self.coordinator.datasource_exchange._relations
        }

        remote_write_apps = {
            relation.app.name
            for relation in self.model.relations["send-remote-write"]
            if relation.app and relation.data
        }

        # the list of datasource exchange relations whose remote we're also remote writing to.
        remote_write_dsx_relations = [
            dsx_relations[app_name]
            for app_name in set(dsx_relations).intersection(remote_write_apps)
        ]

        # grafana UIDs that are connected to this Tempo.
        grafana_uids = set(self._get_grafana_source_uids())

        remote_write_dsx_databags = []
        for relation in remote_write_dsx_relations:
            try:
                datasource = DSExchangeAppData.load(relation.data[relation.app])
                remote_write_dsx_databags.append(datasource)
            except DataValidationError:
                # load() already logs
                continue

        # filter the remote_write_dsx_databags with those that are connected to the same grafana instances Tempo is connected to.
        matching_datasources = [
            datasource
            for databag in remote_write_dsx_databags
            for datasource in databag.datasources
            if datasource.grafana_uid in grafana_uids and datasource.type == PROMETHEUS_DS_TYPE
        ]

        if not matching_datasources:
            # take good care of logging exactly why this happening, as the logic is quite complex and debugging this will be hell
            msg = "service graph disabled."
            missing_rels = []
            if not remote_write_apps:
                missing_rels.append("send-remote-write")
            if not grafana_uids:
                missing_rels.append("grafana-source")
            if not dsx_relations:
                missing_rels.append("receive-datasource")

            if missing_rels:
                msg += f" Missing relations: {missing_rels}."

            if not remote_write_dsx_relations:
                msg += " There are no datasource_exchange relations with a Prometheus/Mimir that we're also remote writing to."
            else:
                msg += " There are no datasource_exchange relations to a Prometheus/Mimir that are datasources to the same grafana instances Tempo is connected to."

            logger.info(msg)
            return {}

        if len(matching_datasources) > 1:
            logger.info(
                "there are multiple datasources that could be used to create the service graph. We assume that all are equivalent."
            )

        # At this point, we can assume any datasource is a valid datasource to use for service graphs.
        matching_datasource = matching_datasources[0]
        return {
            "serviceMap": {
                "datasourceUid": matching_datasource.uid,
            },
        }

    def _build_grafana_source_extra_fields(self) -> Dict[str, Any]:
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

        if not svc_graph_config:
            return {}

        return {
            "httpMethod": "GET",
            **svc_graph_config,
        }


if __name__ == "__main__":  # pragma: nocover
    from ops import main

    main(TempoCoordinatorCharm)  # noqa
