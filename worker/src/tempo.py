#!/usr/bin/env python3
# Copyright 2025 Canonical
# See LICENSE file for licensing details.

"""Tempo workload management objects."""

import logging
import socket
from typing import Dict, Any

import ops
import tenacity
from coordinated_workers.worker import Worker, CONFIG_FILE
from ops.pebble import Layer

logger = logging.getLogger(__name__)


class TempoError(Exception):
    """Base class for custom errors raised by this module."""


class RolesConfigurationError(TempoError):
    """Raised when the worker has an invalid role(s) set in its config."""


class TempoWorker(Worker):
    """Representation of the tempo worker container and workload."""

    SERVICE_START_RETRY_STOP = tenacity.stop_after_delay(60)
    SERVICE_START_RETRY_WAIT = tenacity.wait_fixed(5)

    container_name = "tempo"

    def __init__(self, charm: ops.CharmBase):
        super().__init__(
            charm=charm,
            # name of the container the worker is operating on
            name=self.container_name,
            pebble_layer=self._layer,
            endpoints={"cluster": "tempo-cluster"},
            readiness_check_endpoint=self._readiness_check_endpoint,
            resources_requests=lambda _: {"cpu": "50m", "memory": "200Mi"},
            # container we want to resource-patch
            container_name=self.container_name,
        )

    @staticmethod
    def _readiness_check_endpoint(worker: Worker) -> str:
        """Endpoint for worker readiness checks."""
        scheme = "https" if worker.cluster.get_tls_data() else "http"
        return f"{scheme}://{socket.getfqdn()}:3200/ready"

    @property
    def _worker_config(self) -> Dict[str, Any]:
        """Override property to add unit-specific configurations, like juju topology."""
        config = super()._worker_config
        if "all" in self.roles or "metrics-generator" in self.roles:
            config = self._add_juju_topology(config)

        return config

    def restart(self):
        """Override the worker's restart logic."""
        # if we don't have the remote-write endpoints, don't try to restart the Tempo service
        # as it will never start and we'll be stuck executing the hook.
        # Exit and let collect-unit-status set the charm to blocked.
        roles = self.roles
        if "metrics-generator" in (roles or ()):
            if not self.cluster.get_remote_write_endpoints():
                logger.error(
                    "cannot start this metrics-generator node without remote-write endpoints."
                    "Please relate the coordinator with a prometheus instance."
                )
                return
        super().restart()

    def _add_juju_topology(self, config: Dict[str, Any]):
        """Modify the worker config to add juju topology for `metrics-generator`'s generated metrics."""
        # if `metrics_generator` doesn't exist in config,
        # then it is not enabled, so no point of adding juju topology.
        if "metrics_generator" in config:
            if "registry" not in config["metrics_generator"]:
                config["metrics_generator"]["registry"] = {}
            labels = {
                "juju_{}".format(key): value
                for key, value in self.cluster.juju_topology.as_dict().items()
                if value
            }
            config["metrics_generator"]["registry"]["external_labels"] = labels

        return config

    @staticmethod
    def _layer(worker: Worker) -> Layer:
        """Return the Pebble layer for the Worker.

        This method assumes that worker.roles is valid.
        """

        roles = worker.roles
        if not len(roles) == 1:
            raise RolesConfigurationError(
                f"Worker can only have 1 role configured. {len(roles)} found."
            )
        role = roles[0]
        if role == "all":
            role = "scalable-single-binary"

        # Configure Tempo workload traces
        env = {}
        if tempo_endpoint := worker.cluster.get_workload_tracing_receivers().get(
            "otlp_http", None
        ):
            topology = worker.cluster.juju_topology
            env.update(
                {
                    "OTEL_TRACES_EXPORTER": "otlp",
                    "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT": f"{tempo_endpoint}/v1/traces",
                    "OTEL_RESOURCE_ATTRIBUTES": f"juju_application={topology.application},juju_model={topology.model}"
                    + f",juju_model_uuid={topology.model_uuid},juju_unit={topology.unit},juju_charm={topology.charm_name}",
                }
            )

        return Layer(
            {
                "summary": "tempo worker layer",
                "description": "pebble config layer for tempo worker",
                "services": {
                    "tempo": {
                        "override": "replace",
                        "summary": "tempo worker process",
                        "command": f"/bin/tempo -config.file={CONFIG_FILE} -target {role}",
                        "startup": "enabled",
                        "environment": env,
                    }
                },
            }
        )

    def set_status(self, e: ops.CollectStatusEvent):
        """Register all statuses the Worker collects on this event."""
        return Worker._on_collect_status(self, e)

    def _on_collect_status(self, e: ops.CollectStatusEvent):
        # skip the collect_unit_status event the Worker is observing,
        # to allow the tempo charm to override the priority: see
        # https://github.com/canonical/cos-lib/issues/120
        pass
