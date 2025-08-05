"""Tests for the tempo-api lib requirer and provider classes, excluding their usage in the Tempo Coordinator charm."""

import json
from typing import Union
from unittest.mock import patch

import pytest
from charms.tempo_coordinator_k8s.v0.tempo_api import (
    TempoApiAppData,
    TempoApiProvider,
    TempoApiRequirer,
    TempoApiUrls,
)
from ops import CharmBase
from ops.testing import Context, Relation, State

RELATION_NAME = "app-data-relation"
INTERFACE_NAME = "app-data-interface"

# Note: if this is changed, the TempoApiAppData concrete classes below need to change their constructors to match
SAMPLE_APP_DATA = TempoApiAppData(
    http=TempoApiUrls(
        direct_url="http://www.internal-url-http.com/",
        ingress_url="http://www.ingress-url-http.com/",
    ),
    grpc=TempoApiUrls(
        direct_url="http://www.internal-url-grpc.com/",
        ingress_url="http://www.ingress-url-grpc.com/",
    ),
)
SAMPLE_APP_DATA_NO_INGRESS_URL = TempoApiAppData(
    http=TempoApiUrls(
        direct_url="http://www.internal-url-http.com/",
    ),
    grpc=TempoApiUrls(
        direct_url="http://www.internal-url-grpc.com/",
    ),
)


def data_to_relation_databag(data: TempoApiAppData) -> dict:
    """Convert a TempoApiAppData to the format expected in a Relation's databag."""
    data_dict = data.model_dump(mode="json", by_alias=True, exclude_defaults=True, round_trip=True)
    # Flatten any nested objects to json, since relation databags are str:str mappings
    return {k: json.dumps(v) for k, v in data_dict.items()}


def sample_data_to_tempo_api_publish_args(data: TempoApiAppData) -> dict:
    """Convert a TempoApiAppData to the format expected as arguments to TempoApiProvider.publish()."""
    return {
        "direct_url_http": data.http.direct_url,
        "direct_url_grpc": data.grpc.direct_url,
        "ingress_url_http": data.http.ingress_url,
        "ingress_url_grpc": data.grpc.ingress_url,
    }


class TempoApiProviderCharm(CharmBase):
    META = {
        "name": "provider",
        "provides": {RELATION_NAME: {"interface": RELATION_NAME}},
    }

    def __init__(self, framework):
        super().__init__(framework)
        self.relation_provider = TempoApiProvider(
            self.model.relations, RELATION_NAME, app=self.app
        )


@pytest.fixture()
def tempo_api_provider_context():
    return Context(charm_type=TempoApiProviderCharm, meta=TempoApiProviderCharm.META)


class TempoApiRequirerCharm(CharmBase):
    META = {
        "name": "requirer",
        "requires": {RELATION_NAME: {"interface": "tempo-api", "limit": 1}},
    }

    def __init__(self, framework):
        super().__init__(framework)

        # Skip the validation of relation metadata as it reads a charmcraft.yaml/metadata.yaml file from disk and this
        # mock requirer doesn't have one
        with patch.object(TempoApiRequirer, "_validate_relation_metadata", return_value=None):
            self.relation_requirer = TempoApiRequirer(
                self.model.relations, relation_name=RELATION_NAME
            )


@pytest.fixture()
def tempo_api_requirer_context():
    return Context(charm_type=TempoApiRequirerCharm, meta=TempoApiRequirerCharm.META)


@pytest.mark.parametrize("data", [SAMPLE_APP_DATA, SAMPLE_APP_DATA_NO_INGRESS_URL])
def test_tempo_api_provider_sends_data_correctly(data, tempo_api_provider_context):
    """Tests that a charm using TempoApiProvider sends the correct data during publish."""
    # Arrange
    tempo_api_relation = Relation(RELATION_NAME, INTERFACE_NAME, local_app_data={})
    relations = [tempo_api_relation]
    state = State(relations=relations, leader=True)

    # Act
    with tempo_api_provider_context(
        # construct a charm using an event that won't trigger anything here
        tempo_api_provider_context.on.update_status(),
        state=state,
    ) as manager:
        manager.charm.relation_provider.publish(**sample_data_to_tempo_api_publish_args(data))

        # Assert
        # Convert local_app_data to TempoApiAppData for comparison
        tempo_api_relation_out = manager.ops.state.get_relation(tempo_api_relation.id)
        actual = TempoApiAppData.model_validate(
            {str(k): json.loads(v) for k, v in tempo_api_relation_out.local_app_data.items()}
        )

        assert actual == data


@pytest.mark.parametrize(
    "relations, expected_data",
    [
        # no relations
        ([], None),
        # one empty relation
        (
            [Relation(RELATION_NAME, INTERFACE_NAME, remote_app_data={})],
            None,
        ),
        # one populated relation
        (
            [
                Relation(
                    RELATION_NAME,
                    INTERFACE_NAME,
                    remote_app_data=data_to_relation_databag(SAMPLE_APP_DATA),
                )
            ],
            SAMPLE_APP_DATA,
        ),
        # one populated relation without ingress_url
        (
            [
                Relation(
                    RELATION_NAME,
                    INTERFACE_NAME,
                    remote_app_data=data_to_relation_databag(SAMPLE_APP_DATA_NO_INGRESS_URL),
                )
            ],
            SAMPLE_APP_DATA_NO_INGRESS_URL,
        ),
    ],
)
def test_tempo_api_requirer_get_data(relations, expected_data, tempo_api_requirer_context):
    """Tests that TempoApiRequirer.get_data() returns correctly."""
    state = State(
        relations=relations,
        leader=False,
    )

    with tempo_api_requirer_context(
        tempo_api_requirer_context.on.update_status(), state=state
    ) as manager:
        charm = manager.charm

        data = charm.relation_requirer.get_data()
        assert are_app_data_equal(data, expected_data)


def are_app_data_equal(data1: Union[TempoApiAppData, None], data2: Union[TempoApiAppData, None]):
    """Compare two TempoApiRequirer objects, tolerating when one or both is None."""
    if data1 is None and data2 is None:
        return True
    if data1 is None or data2 is None:
        return False
    return data1.model_dump() == data2.model_dump()
