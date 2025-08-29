# Copyright 2023 Canonical
# See LICENSE file for licensing details.
"""Nginx workload."""

from typing import Dict, List, cast

from charms.tempo_coordinator_k8s.v0.tracing import (
    ReceiverProtocol,
    TransportProtocolType,
    receiver_protocol_to_transport_protocol,
)
from coordinated_workers.nginx import NginxLocationConfig, NginxUpstream

from tempo import Tempo
from tempo_config import TempoRole


def upstreams() -> List[NginxUpstream]:
    upstreams = []

    for role, ports in (
        (TempoRole.distributor, Tempo.receiver_ports),
        (TempoRole.query_frontend, Tempo.server_ports),
    ):
        for protocol, port in ports.items():
            protocol = protocol.replace("_", "-")
            upstreams.append(NginxUpstream(protocol, port, role))

    return upstreams


def server_ports_to_locations() -> Dict[int, List[NginxLocationConfig]]:
    locations = {}
    all_protocol_ports = {**Tempo.receiver_ports, **Tempo.server_ports}
    for protocol, port in all_protocol_ports.items():
        upstream = protocol.replace("_", "-")
        is_grpc = _is_protocol_grpc(protocol)
        locations.update(
            {port: [NginxLocationConfig(path="/", backend=upstream, is_grpc=is_grpc)]}
        )

    return locations


def _is_protocol_grpc(protocol: str) -> bool:
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
