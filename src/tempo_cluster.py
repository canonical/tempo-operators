#!/usr/bin/env python3
# Copyright 2024 Canonical
# See LICENSE file for licensing details.

"""This module contains an endpoint wrapper class for the provider side of the ``tempo-cluster`` relation.

As this relation is cluster-internal and not intended for third-party charms to interact with `tempo-coordinator-k8s`, its only user will be the tempo-coordinator-k8s charm. As such, it does not live in a charm lib as most other relation endpoint wrappers do.
"""


import json
import logging
from collections import defaultdict
from enum import Enum
from typing import Any, Dict, Iterable, List, MutableMapping, Optional, Set

import ops
import pydantic
from ops import Object
from pydantic import BaseModel, ConfigDict

log = logging.getLogger("tempo_cluster")

DEFAULT_ENDPOINT_NAME = "tempo-cluster"
BUILTIN_JUJU_KEYS = {"ingress-address", "private-address", "egress-subnets"}
TEMPO_CONFIG_FILE = "/etc/tempo/tempo-config.yaml"
TEMPO_CERT_FILE = "/etc/tempo/server.cert"
TEMPO_KEY_FILE = "/etc/tempo/private.key"
TEMPO_CLIENT_CA_FILE = "/etc/tempo/ca.cert"


class TempoRole(str, Enum):
    """Tempo component role names."""

    overrides_exporter = "overrides-exporter"
    query_scheduler = "query-scheduler"
    flusher = "flusher"
    query_frontend = "query-frontend"
    querier = "querier"
    store_gateway = "store-gateway"
    ingester = "ingester"
    distributor = "distributor"
    ruler = "ruler"
    alertmanager = "alertmanager"
    compactor = "compactor"

    # meta-roles
    read = "read"
    write = "write"
    backend = "backend"
    all = "all"


META_ROLES = {
    TempoRole.read: (TempoRole.query_frontend, TempoRole.querier),
    TempoRole.write: (TempoRole.distributor, TempoRole.ingester),
    TempoRole.backend: (
        TempoRole.store_gateway,
        TempoRole.compactor,
        TempoRole.ruler,
        TempoRole.alertmanager,
        TempoRole.query_scheduler,
        TempoRole.overrides_exporter,
    ),
    TempoRole.all: list(TempoRole),
}


def expand_roles(roles: Iterable[TempoRole]) -> Set[TempoRole]:
    """Expand any meta roles to their 'atomic' equivalents."""
    expanded_roles = set()
    for role in roles:
        if role in META_ROLES:
            expanded_roles.update(META_ROLES[role])
        else:
            expanded_roles.add(role)
    return expanded_roles


class TempoClusterProvider(Object):
    """``tempo-cluster`` provider endpoint wrapper."""

    def __init__(
        self,
        charm: ops.CharmBase,
        key: Optional[str] = None,
        endpoint: str = DEFAULT_ENDPOINT_NAME,
    ):
        super().__init__(charm, key)
        self._charm = charm
        self._relations = self.model.relations[endpoint]

    def publish_data(
        self,
        tempo_config: Dict[str, Any],
        loki_endpoints: Optional[Dict[str, str]] = None,
    ) -> None:
        """Publish the tempo config and loki endpoints to all related tempo worker clusters."""
        for relation in self._relations:
            if relation:
                local_app_databag = TempoClusterProviderAppData(
                    tempo_config=tempo_config, loki_endpoints=loki_endpoints
                )
                local_app_databag.dump(relation.data[self.model.app])

    def gather_roles(self) -> Dict[TempoRole, int]:
        """Go through the worker's app databags and sum the available application roles."""
        data = {}
        for relation in self._relations:
            if relation.app:
                remote_app_databag = relation.data[relation.app]
                try:
                    worker_roles: List[TempoRole] = TempoClusterRequirerAppData.load(
                        remote_app_databag
                    ).roles
                except DataValidationError as e:
                    log.info(f"invalid databag contents: {e}")
                    worker_roles = []

                # the number of units with each role is the number of remote units
                role_n = len(relation.units)  # exclude this unit

                for role in expand_roles(worker_roles):
                    if role not in data:
                        data[role] = 0
                    data[role] += role_n
        return data

    def gather_addresses_by_role(self) -> Dict[str, Set[str]]:
        """Go through the worker's unit databags to collect all the addresses published by the units, by role."""
        data = defaultdict(set)
        for relation in self._relations:
            if not relation.app:
                log.debug(f"skipped {relation} as .app is None")
                continue

            try:
                worker_app_data = TempoClusterRequirerAppData.load(relation.data[relation.app])
                worker_roles = set(worker_app_data.roles)
            except DataValidationError as e:
                log.info(f"invalid databag contents: {e}")
                continue

            for worker_unit in relation.units:
                try:
                    worker_data = TempoClusterRequirerUnitData.load(relation.data[worker_unit])
                    unit_address = worker_data.address
                    for role in worker_roles:
                        data[role].add(unit_address)
                except DataValidationError as e:
                    log.info(f"invalid databag contents: {e}")
                    continue

        return data

    def gather_addresses(self) -> Set[str]:
        """Go through the worker's unit databags to collect all the addresses published by the units."""
        data = set()
        addresses_by_role = self.gather_addresses_by_role()
        for role, address_set in addresses_by_role.items():
            data.update(address_set)

        return data

    def get_datasource_address(self) -> Optional[str]:
        """Get datasource address."""
        addresses_by_role = self.gather_addresses_by_role()
        if address_set := addresses_by_role.get("ruler", None):
            return address_set.pop()

    def gather_topology(self) -> List[Dict[str, str]]:
        """Gather Topology."""
        data = []
        for relation in self._relations:
            if not relation.app:
                continue

            for worker_unit in relation.units:
                try:
                    worker_data = TempoClusterRequirerUnitData.load(relation.data[worker_unit])
                    unit_address = worker_data.address
                except DataValidationError as e:
                    log.info(f"invalid databag contents: {e}")
                    continue
                worker_topology = {
                    "unit": worker_unit.name,
                    "app": worker_unit.app.name,
                    "address": unit_address,
                }
                data.append(worker_topology)

        return data


class DatabagModel(BaseModel):
    """Base databag model."""

    model_config = ConfigDict(
        # Allow instantiating this class by field name (instead of forcing alias).
        populate_by_name=True,
        # Custom config key: whether to nest the whole datastructure (as json)
        # under a field or spread it out at the toplevel.
        _NEST_UNDER=None,
    )  # type: ignore
    """Pydantic config."""

    @classmethod
    def load(cls, databag: MutableMapping[str, str]):
        """Load this model from a Juju databag."""
        nest_under = cls.model_config.get("_NEST_UNDER")
        if nest_under:
            return cls.parse_obj(json.loads(databag[nest_under]))

        try:
            data = {k: json.loads(v) for k, v in databag.items() if k not in BUILTIN_JUJU_KEYS}
        except json.JSONDecodeError as e:
            msg = f"invalid databag contents: expecting json. {databag}"
            log.info(msg)
            raise DataValidationError(msg) from e

        try:
            return cls.parse_raw(json.dumps(data))  # type: ignore
        except pydantic.ValidationError as e:
            msg = f"failed to validate databag: {databag}"
            log.info(msg, exc_info=True)
            raise DataValidationError(msg) from e

    def dump(self, databag: Optional[MutableMapping[str, str]] = None, clear: bool = True):
        """Write the contents of this model to Juju databag.

        :param databag: the databag to write the data to.
        :param clear: ensure the databag is cleared before writing it.
        """
        if clear and databag:
            databag.clear()

        if databag is None:
            databag = {}
        nest_under = self.model_config.get("_NEST_UNDER")
        if nest_under:
            databag[nest_under] = self.json()

        dct = self.model_dump(by_alias=True)
        for key, field in self.model_fields.items():  # type: ignore
            value = dct[key]
            databag[field.alias or key] = json.dumps(value)
        return databag


class JujuTopology(pydantic.BaseModel):
    """JujuTopology."""

    model: str
    unit: str
    # ...


class TempoClusterProviderAppData(DatabagModel):
    """TempoClusterProviderAppData."""

    tempo_config: Dict[str, Any]
    loki_endpoints: Optional[Dict[str, str]] = None


class TempoClusterRequirerAppData(DatabagModel):
    """TempoClusterRequirerAppData."""

    roles: List[TempoRole]


class TempoClusterRequirerUnitData(DatabagModel):
    """TempoClusterRequirerUnitData."""

    juju_topology: JujuTopology
    address: str


class TempoClusterError(Exception):
    """Base class for exceptions raised by this module."""


class DataValidationError(TempoClusterError):
    """Raised when relation databag validation fails."""


class DatabagAccessPermissionError(TempoClusterError):
    """Raised when a follower attempts to write leader settings."""
