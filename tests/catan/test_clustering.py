import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest
import scenario
from catan import App, Catan
from scenario import Container, State

from tempo_cluster import TempoRole, TempoClusterRequirerAppData

os.environ["CHARM_TRACING_ENABLED"] = "0"

# everyone has their own
REPOS_ROOT = Path("~").expanduser() / "canonical"
FACADE_CHARM_ROOT = REPOS_ROOT / "charm-relation-interfaces" / "facade_charm"


def get_facade(name="facade"):
    meta = Path(FACADE_CHARM_ROOT) / 'charmcraft.yaml'
    if not meta.exists():
        raise RuntimeError(f"{meta} not found: run facade_charm.update_endpoints before running this test")

    facade = App.from_path(
        FACADE_CHARM_ROOT,
        name=name,
    )
    return facade


@pytest.fixture
def s3_facade() -> App:
    return get_facade("s3")


def prep_s3_facade(catan: Catan):
    catan.run_action(
        scenario.Action(
            "update",
            params={
                "endpoint": json.dumps("provides-s3"),
                "app_data": json.dumps(
                    {
                        "access-key": "key",
                        "bucket": "tempo",
                        "endpoint": "http://1.2.3.4:9000",
                        "secret-key": "soverysecret",
                    }
                ),
            },
        ),
        catan.get_app("s3"),
    )


@pytest.fixture
def ca_facade() -> App:
    return get_facade("ca")


def prep_ca_facade(catan: Catan):
    catan.run_action(
        scenario.Action(
            "update",
            params={"endpoint": json.dumps("provides-certificates"), "app_data": json.dumps("")},
        ),
        catan.get_app("ca"),
    )


@pytest.fixture
def traefik_facade() -> App:
    return get_facade("traefik")


def prep_traefik_facade(catan: Catan):
    catan.run_action(
        scenario.Action(
            "update",
            params={
                "endpoint": json.dumps("provides-traefik_route"),
                "app_data": json.dumps({"external_host": "1.2.3.4", "scheme": "http"}),
            },
        ),
        catan.get_app("ca"),
    )


@pytest.fixture
def tempo_coordinator():
    tempo = App.from_path(
        REPOS_ROOT / "tempo-coordinator-k8s-operator",
        patches=[
            patch("charm.KubernetesServicePatch"),
            patch("lightkube.core.client.GenericSyncClient"),
            patch("charm.TempoCoordinatorCharm._update_server_cert"),
            # patch("tempo.Tempo.is_ready", new=lambda _: True),
        ],
        name="tempo",
    )
    yield tempo


@pytest.fixture
def tempo_worker():
    tempo = App.from_path(
        REPOS_ROOT / "tempo-worker-k8s-operator",
        name="worker",
    )
    yield tempo


@pytest.fixture(scope="function")
def s3_config():
    return {
        "access-key": "key",
        "bucket": "tempo",
        "endpoint": "http://1.2.3.4:9000",
        "secret-key": "soverysecret",
    }


@pytest.fixture(scope="function")
def s3(s3_config):
    return scenario.Relation(
        "s3",
        remote_app_data=s3_config,
        local_unit_data={"bucket": "tempo"},
    )


@pytest.fixture(scope="function")
def all_worker():
    return scenario.Relation(
        "tempo-cluster",
        remote_app_data=TempoClusterRequirerAppData(role=TempoRole.all).dump(),
    )


@pytest.fixture(scope="function")
def tempo_peers():
    return scenario.PeerRelation("peers")


@pytest.fixture
def tempo_coordinator_state(tempo_peers):
    return State(
        relations=[tempo_peers]
    )


@pytest.fixture
def tempo_worker_state():
    return State(
        containers=[Container(name="tempo", can_connect=True)],
    )


@pytest.fixture
def update_s3_facade_action():
    return scenario.Action(
        "update",
        params={
            "endpoint": "provide-s3",
            "app_data": json.dumps({
                "access-key": "key",
                "bucket": "tempo",
                "endpoint": "http://1.2.3.4:9000",
                "secret-key": "soverysecret",
            })
        }
    )


def test_monolithic_deployment(tempo_coordinator, tempo_coordinator_state, tempo_worker,
                               tempo_worker_state, s3_facade, update_s3_facade_action):
    c = Catan()

    c.deploy(s3_facade)
    c.deploy(tempo_coordinator, state_template=tempo_coordinator_state)
    c.deploy(tempo_worker, state_template=tempo_worker_state)

    c.integrate(tempo_coordinator, "tempo-cluster", tempo_worker, "tempo-cluster")
    c.integrate(tempo_coordinator, "s3", s3_facade, "provide-s3")
    c.run_action(update_s3_facade_action, s3_facade)

    c.settle()

    c.check_status(tempo_coordinator, name='active')
