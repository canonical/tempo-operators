#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Charmed Operator for Tempo; a lightweight object storage based tracing backend."""
import logging
import socket
from pathlib import Path
from subprocess import CalledProcessError, getoutput
from typing import Dict, Optional, Set, Tuple, cast, get_args

import ops
from charms.grafana_k8s.v0.grafana_source import GrafanaSourceProvider
from charms.tempo_k8s.v1.charm_tracing import trace_charm
from charms.tempo_k8s.v2.tracing import (
    ReceiverProtocol,
    RequestEvent,
    TracingEndpointProvider,
    TransportProtocolType,
    receiver_protocol_to_transport_protocol,
)
from charms.traefik_route_k8s.v0.traefik_route import TraefikRouteRequirer
from cosl.coordinated_workers.coordinator import ClusterRolesConfig, Coordinator
from cosl.coordinated_workers.nginx import CA_CERT_PATH, CERT_PATH, KEY_PATH
from ops.charm import CharmBase, RelationEvent
from ops.main import main

from nginx_config import NginxConfig
from tempo import Tempo
from tempo_config import TEMPO_ROLES_CONFIG

logger = logging.getLogger(__name__)


@trace_charm(
    tracing_endpoint="tempo_otlp_http_endpoint",
    server_cert="server_ca_cert",
    extra_types=(Tempo, TracingEndpointProvider, Coordinator, ClusterRolesConfig),
)
class TempoCoordinatorCharm(CharmBase):
    """Charmed Operator for Tempo; a distributed tracing backend."""

    def __init__(self, *args):
        super().__init__(*args)

        self.ingress = TraefikRouteRequirer(self, self.model.get_relation("ingress"), "ingress")  # type: ignore
        self.tempo = Tempo(
            requested_receivers=self._requested_receivers,
            retention_period_hours=self.trace_retention_period_hours,
        )
        # set the open ports for this unit
        self.unit.set_ports(*self.tempo.all_ports.values())
        self.coordinator = Coordinator(
            charm=self,
            roles_config=TEMPO_ROLES_CONFIG,
            s3_bucket_name=Tempo.s3_bucket_name,
            external_url=self._external_url,
            worker_metrics_port=self.tempo.tempo_http_server_port,
            endpoints={
                "certificates": "certificates",
                "cluster": "tempo-cluster",
                "grafana-dashboards": "grafana-dashboard",
                "logging": "logging",
                "metrics": "metrics-endpoint",
                "s3": "s3",
                "tracing": "self-tracing",
            },
            nginx_config=NginxConfig(server_name=self.hostname).config,
            workers_config=self.tempo.config,
            tracing_receivers=self.requested_receivers_urls,
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
        )

        self.tracing = TracingEndpointProvider(self, external_url=self._external_url)

        # refuse to handle any other event as we can't possibly know what to do.
        if not self.coordinator.can_handle_events:
            # logging will be handled by `self.coordinator` for each of the above circumstances.
            return

        # lifecycle
        self.framework.observe(self.on.leader_elected, self._on_leader_elected)
        self.framework.observe(self.on.list_receivers_action, self._on_list_receivers_action)

        # ingress
        ingress = self.on["ingress"]
        self.framework.observe(ingress.relation_created, self._on_ingress_relation_created)
        self.framework.observe(ingress.relation_joined, self._on_ingress_relation_joined)
        self.framework.observe(self.ingress.on.ready, self._on_ingress_ready)

        # tracing
        self.framework.observe(self.tracing.on.request, self._on_tracing_request)
        self.framework.observe(self.tracing.on.broken, self._on_tracing_broken)

        # tls
        self.framework.observe(
            self.coordinator.cert_handler.on.cert_changed, self._on_cert_handler_changed
        )

    ######################
    # UTILITY PROPERTIES #
    ######################
    @property
    def hostname(self) -> str:
        """Unit's hostname."""
        return socket.getfqdn()

    @property
    def _external_http_server_url(self) -> str:
        """External url of the http(s) server."""
        return f"{self._external_url}:{self.tempo.tempo_http_server_port}"

    @property
    def _external_url(self) -> str:
        """Return the external url."""
        if self.ingress.is_ready():
            ingress_url = f"{self.ingress.scheme}://{self.ingress.external_host}"
            logger.debug("This unit's ingress URL: %s", ingress_url)
            return ingress_url

        # If we do not have an ingress, then use the pod hostname.
        # The reason to prefer this over the pod name (which is the actual
        # hostname visible from the pod) or a K8s service, is that those
        # are routable virtually exclusively inside the cluster (as they rely)
        # on the cluster's DNS service, while the ip address is _sometimes_
        # routable from the outside, e.g., when deploying on MicroK8s on Linux.
        return self._internal_url

    @property
    def _internal_url(self) -> str:
        """Returns workload's FQDN."""
        scheme = "http"
        if self.are_certificates_on_disk:
            scheme = "https"

        return f"{scheme}://{self.hostname}"

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

    ##################
    # EVENT HANDLERS #
    ##################
    def _on_tracing_broken(self, _):
        """Update tracing relations' databags once one relation is removed."""
        self._update_tracing_relations()

    def _on_cert_handler_changed(self, e: ops.RelationChangedEvent):

        # tls readiness change means config change.
        # sync scheme change with traefik and related consumers
        self._configure_ingress()

        # sync the server CA cert with the charm container.
        # technically, because of charm tracing, this will be called first thing on each event
        self._update_server_ca_cert()

        # update relations to reflect the new certificate
        self._update_tracing_relations()

    def _on_tracing_request(self, e: RequestEvent):
        """Handle a remote requesting a tracing endpoint."""
        logger.debug(f"received tracing request from {e.relation.app}: {e.requested_receivers}")
        self._update_tracing_relations()

    def _on_ingress_relation_created(self, _: RelationEvent):
        self._configure_ingress()

    def _on_ingress_relation_joined(self, _: RelationEvent):
        self._configure_ingress()

    def _on_leader_elected(self, _: ops.LeaderElectedEvent):
        # as traefik_route goes through app data, we need to take lead of traefik_route if our leader dies.
        self._configure_ingress()

    def _on_ingress_ready(self, _event):
        # whenever there's a change in ingress, we need to update all tracing relations
        self._update_tracing_relations()

    def _on_ingress_revoked(self, _event):
        # whenever there's a change in ingress, we need to update all tracing relations
        self._update_tracing_relations()

    def _on_list_receivers_action(self, event: ops.ActionEvent):
        res = {}
        for receiver in self._requested_receivers():
            res[receiver.replace("_", "-")] = self.get_receiver_url(receiver)
        event.set_results(res)

    ###################
    # UTILITY METHODS #
    ###################
    def _configure_ingress(self) -> None:
        """Make sure the traefik route and tracing relation data are up-to-date."""
        if not self.unit.is_leader():
            return

        if self.ingress.is_ready():
            self.ingress.submit_to_traefik(
                self._ingress_config, static=self._static_ingress_config
            )
            if self.ingress.external_host:
                self._update_tracing_relations()

        # notify the cluster
        self.coordinator.update_cluster()

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

        self.coordinator.update_cluster()

    def _requested_receivers(self) -> Tuple[ReceiverProtocol, ...]:
        """List what receivers we should activate, based on the active tracing relations and config-enabled extra receivers."""
        # we start with the sum of the requested endpoints from the requirers
        requested_protocols = set(self.tracing.requested_protocols())

        # update with enabled extra receivers
        requested_protocols.update(self.enabled_receivers)
        # and publish only those we support
        requested_receivers = requested_protocols.intersection(set(self.tempo.receiver_ports))
        return tuple(requested_receivers)

    @property
    def trace_retention_period_hours(self) -> int:
        """Trace retention period for the compactor."""
        # if unset, default to 30 days
        trace_retention_period_hours = cast(int, self.config.get("retention_period_hours", 720))
        return trace_retention_period_hours

    def server_ca_cert(self) -> str:
        """For charm tracing."""
        self._update_server_ca_cert()
        return self.tempo.tls_ca_path

    def _update_server_ca_cert(self) -> None:
        """Server CA certificate for charm tracing tls, if tls is enabled."""
        server_ca_cert = Path(self.tempo.tls_ca_path)
        if self.coordinator.tls_available:
            if self.coordinator.cert_handler.ca_cert:
                server_ca_cert.parent.mkdir(parents=True, exist_ok=True)
                server_ca_cert.write_text(self.coordinator.cert_handler.ca_cert)
        else:  # tls unavailable: delete local cert
            server_ca_cert.unlink(missing_ok=True)

    def tempo_otlp_http_endpoint(self) -> Optional[str]:
        """Endpoint at which the charm tracing information will be forwarded."""
        # the charm container and the tempo workload container have apparently the same
        # IP, so we can talk to tempo at localhost.
        if self.coordinator and self.coordinator.tracing.is_ready():
            return self.coordinator.tracing.get_endpoint("otlp_http")
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
                ] = {"loadBalancer": {"servers": [{"url": f"h2c://{self.hostname}:{port}"}]}}
            else:
                # anything else, including secured GRPC, can use _internal_url
                # ref https://doc.traefik.io/traefik/v2.0/user-guides/grpc/#with-https
                http_services[
                    f"juju-{self.model.name}-{self.model.app.name}-service-{sanitized_protocol}"
                ] = {"loadBalancer": {"servers": [{"url": f"{self._internal_url}:{port}"}]}}
        return {
            "http": {
                "routers": http_routers,
                "services": http_services,
            },
        }

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
                self.coordinator.hostname
                if protocol_type == TransportProtocolType.grpc
                else self.coordinator._internal_url
            )

        return f"{url}:{receiver_port}"

    def is_workload_ready(self):
        """Whether the tempo built-in readiness check reports 'ready'."""
        if self.coordinator.tls_available:
            tls, s = f" --cacert {self.tempo.tls_ca_path}", "s"
        else:
            tls = s = ""

        # cert is for fqdn/ingress, not for IP
        cmd = f"curl{tls} http{s}://{self.coordinator.hostname}:{self.tempo.tempo_http_server_port}/ready"

        try:
            out = getoutput(cmd).split("\n")[-1]
        except (CalledProcessError, IndexError):
            return False
        return out == "ready"


if __name__ == "__main__":  # pragma: nocover
    main(TempoCoordinatorCharm)
