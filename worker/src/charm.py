#!/usr/bin/env python3
# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Tempo worker charm.
This charm deploys a Tempo worker application on k8s Juju models.
"""

import logging

from ops import CollectStatusEvent
from ops.charm import CharmBase
from ops.model import BlockedStatus, ActiveStatus

from tempo import TempoWorker

logger = logging.getLogger(__name__)

_LEGACY_WORKER_PORTS = 3200, 4317, 4318, 9411, 14268, 7946, 9096, 14250


class TempoWorkerK8SOperatorCharm(CharmBase):
    """A Juju Charmed Operator for Tempo."""

    def __init__(self, *args):
        super().__init__(*args)
        self.worker = TempoWorker(self)

        # if the worker has received some ports from the coordinator,
        # it's in charge of ensuring they're opened.
        if not self.worker.cluster.get_worker_ports():
            # legacy behaviour fallback: older interface versions didn't tell us which
            # ports we should be opening, so we opened all of them.
            # This can happen when talking to an old coordinator revision, or
            # if the coordinator hasn't published its data yet.
            logger.info(
                "Cluster interface hasn't published a list of worker ports (yet?). "
                "If this issue persists after the cluster has settled, you should "
                "upgrade the coordinator to a newer revision. Falling back now to "
                "legacy behaviour and opening all ports."
            )
            self.unit.set_ports(*_LEGACY_WORKER_PORTS)

        # event handling
        self.framework.observe(self.on.collect_unit_status, self._on_collect_status)

    def _on_collect_status(self, e: CollectStatusEvent):
        # add Tempo worker-specific statuses
        roles = self.worker.roles
        if roles and len(roles) > 1:
            e.add_status(
                BlockedStatus(f"cannot have more than 1 enabled role: {roles}")
            )
        if (
            roles
            and self.worker.cluster.relation
            and not self.worker.cluster.get_remote_write_endpoints()
        ):
            if "all" in roles:
                e.add_status(
                    ActiveStatus(
                        "metrics-generator disabled. No prometheus remote-write relation configured on the coordinator"
                    )
                )
            elif "metrics-generator" in roles:
                e.add_status(
                    BlockedStatus(
                        "No prometheus remote-write relation configured on the coordinator"
                    )
                )

        # the worker will set its status after we've set ours,
        # so in case of a conflict ours will prevail
        self.worker.set_status(e)


if __name__ == "__main__":  # pragma: nocover
    import ops

    ops.main(TempoWorkerK8SOperatorCharm)
