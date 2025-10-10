"""Tests that assert TempoCoordinatorCharm is wired up correctly to be a tempo-api provider."""

import json
from typing import Optional, Tuple
from unittest.mock import PropertyMock, patch

from ops.testing import Relation, State

from tempo import Tempo

RELATION_NAME = "tempo-api"
INTERFACE_NAME = "tempo_metadata"

TEMPO_COORDINATOR_URL = "http://needs-changing.com"
INGRESS_URL = "http://www.ingress-url.com"


def local_app_data_relation_state(
    leader: bool, local_app_data: Optional[dict] = None
) -> Tuple[Relation, State]:
    """Return a testing State that has a single relation with the given local_app_data."""
    if local_app_data is None:
        local_app_data = {}
    else:
        # Scenario might edit this dict, and it could be used elsewhere
        local_app_data = dict(local_app_data)

    relation = Relation(RELATION_NAME, INTERFACE_NAME, local_app_data=local_app_data)
    relations = [relation]

    state = State(
        relations=relations,
        leader=leader,
    )

    return relation, state


@patch(
    "charm.TempoCoordinatorCharm._internal_url",
    PropertyMock(return_value=TEMPO_COORDINATOR_URL),
)
def test_provider_sender_sends_data_on_relation_joined(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    """Tests that a charm using TempoApiProvider sends the correct data on a relation joined event."""
    # Arrange
    tempo_api = Relation(RELATION_NAME, INTERFACE_NAME)
    relations = [
        tempo_api,
        s3,
        all_worker,
    ]

    state = State(
        relations=relations,
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    expected = {
        "http": json.dumps(
            {
                "direct_url": TEMPO_COORDINATOR_URL
                + f":{Tempo.server_ports['tempo_http']}/",
            }
        ),
        "grpc": json.dumps(
            {
                "direct_url": TEMPO_COORDINATOR_URL
                + f":{Tempo.server_ports['tempo_grpc']}/",
            }
        ),
    }

    # Act
    state_out = context.run(context.on.relation_joined(tempo_api), state=state)

    # Assert
    assert state_out.get_relation(tempo_api.id).local_app_data == expected


@patch(
    "charm.TempoCoordinatorCharm._external_url", PropertyMock(return_value=INGRESS_URL)
)
@patch(
    "charm.TempoCoordinatorCharm._internal_url",
    PropertyMock(return_value=TEMPO_COORDINATOR_URL),
)
def test_provider_sender_sends_data_with_ingress_url_on_relation_joined(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    """Tests that a charm using TempoApiProvider with an external url sends the correct data."""
    # Arrange
    tempo_api = Relation(RELATION_NAME, INTERFACE_NAME)
    relations = [
        tempo_api,
        s3,
        all_worker,
    ]

    state = State(
        relations=relations,
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    expected = {
        "http": json.dumps(
            {
                "direct_url": TEMPO_COORDINATOR_URL
                + f":{Tempo.server_ports['tempo_http']}/",
                "ingress_url": INGRESS_URL + f":{Tempo.server_ports['tempo_http']}/",
            }
        ),
        "grpc": json.dumps(
            {
                "direct_url": TEMPO_COORDINATOR_URL
                + f":{Tempo.server_ports['tempo_grpc']}/",
                "ingress_url": INGRESS_URL + f":{Tempo.server_ports['tempo_grpc']}/",
            }
        ),
    }

    # Act
    state_out = context.run(context.on.relation_joined(tempo_api), state=state)

    # Assert
    assert state_out.get_relation(tempo_api.id).local_app_data == expected


@patch(
    "charm.TempoCoordinatorCharm._internal_url",
    PropertyMock(return_value=TEMPO_COORDINATOR_URL),
)
def test_provider_sends_data_on_leader_elected(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):
    """Tests that a charm using TempoApiProvider sends data on a leader elected event."""
    # Arrange
    tempo_api = Relation(RELATION_NAME, INTERFACE_NAME)
    relations = [
        tempo_api,
        s3,
        all_worker,
    ]

    state = State(
        relations=relations,
        leader=True,
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    expected = {
        "http": json.dumps(
            {
                "direct_url": TEMPO_COORDINATOR_URL
                + f":{Tempo.server_ports['tempo_http']}/",
            }
        ),
        "grpc": json.dumps(
            {
                "direct_url": TEMPO_COORDINATOR_URL
                + f":{Tempo.server_ports['tempo_grpc']}/",
            }
        ),
    }

    # Act
    state_out = context.run(context.on.leader_elected(), state=state)

    # Assert
    assert state_out.get_relation(tempo_api.id).local_app_data == expected
