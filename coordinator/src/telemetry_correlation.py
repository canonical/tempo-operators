#!/usr/bin/env python3
# Copyright 2024 Canonical Ltd.
# See LICENSE file for licensing details.

"""Utilities for working with telemetry correlation."""

import json
import logging
from typing import Dict, Optional

import ops

from coordinated_workers.coordinator import Coordinator
from cosl.interfaces.datasource_exchange import (
    DSExchangeAppData,
    GrafanaDatasource,
)
from cosl.interfaces.utils import DataValidationError


logger = logging.getLogger(__name__)


class TelemetryCorrelation:
    """Logic to work with datasource-exchange."""

    def __init__(self, charm: ops.CharmBase, coordinator: Coordinator):
        self._charm = charm
        self._coordinator = coordinator

    def find_datasource(
        self, endpoint: str, datasource_type: str
    ) -> Optional[GrafanaDatasource]:
        """Find a datasource we obtained through datasource-exchange.

        Such that: its remote is the same as the remote we have on this endpoint.
        """
        dsx_relations = {
            relation.app.name: relation
            for relation in self._coordinator.datasource_exchange._relations
        }

        remote_apps_on_endpoint = {
            relation.app.name
            for relation in self._charm.model.relations[endpoint]
            if relation.app and relation.data
        }

        # the list of datasource exchange relations whose remote we're also remote writing to.
        remote_write_dsx_relations = [
            dsx_relations[app_name]
            for app_name in set(dsx_relations).intersection(remote_apps_on_endpoint)
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
            if datasource.grafana_uid in grafana_uids
            and datasource.type == datasource_type
        ]

        if not matching_datasources:
            # take good care of logging exactly why this happening, as the logic is quite complex and debugging this will be hell
            msg = "service graph disabled."
            missing_rels = []
            if not remote_apps_on_endpoint:
                missing_rels.append(endpoint)
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
            return None

        if len(matching_datasources) > 1:
            logger.info(
                "there are multiple datasources that could be used to create the service graph. We assume that all are equivalent."
            )

        # At this point, we can assume any datasource is a valid datasource to use for service graphs.
        return matching_datasources[0]

    def _get_grafana_source_uids(self) -> Dict[str, Dict[str, str]]:
        """Helper method to retrieve the databags of any grafana-source relations.

        Duplicate implementation of GrafanaSourceProvider.get_source_uids() to use in the
        situation where we want to access relation data when the GrafanaSourceProvider object
        is not yet initialised.
        """
        uids = {}
        for rel in self._charm.model.relations.get("grafana-source", []):
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
