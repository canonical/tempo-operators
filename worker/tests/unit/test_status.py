from contextlib import contextmanager
from functools import partial
from unittest.mock import patch

import pytest
from ops import ActiveStatus
from ops import BlockedStatus
from scenario import State, Container, Relation

from tests.unit.conftest import _urlopen_patch
import json


from tests.unit.conftest import UPDATE_CA_CERTS_EXEC_OUTPUT
from tests.unit.helpers import set_role

tempo_container = Container(
    "tempo", can_connect=True, execs={UPDATE_CA_CERTS_EXEC_OUTPUT}
)


@contextmanager
def endpoint_starting(tls: bool = False):
    with patch(
        "urllib.request.urlopen",
        new=partial(_urlopen_patch, tls=tls, resp="foo\nStarting: 10\n bar"),
    ):
        yield


@contextmanager
def endpoint_ready(tls: bool = False):
    with patch(
        "urllib.request.urlopen", new=partial(_urlopen_patch, tls=tls, resp="ready")
    ):
        yield


@contextmanager
def config_on_disk():
    with patch(
        "coordinated_workers.worker.Worker._running_worker_config",
        new=lambda _: True,
    ):
        yield


@pytest.mark.parametrize(
    "role_str, expected",
    (
        (
            "all",
            ActiveStatus(
                "metrics-generator disabled. No prometheus remote-write relation configured on the coordinator"
            ),
        ),
        (
            "metrics-generator",
            BlockedStatus(
                "No prometheus remote-write relation configured on the coordinator"
            ),
        ),
    ),
)
def test_status_remote_write_endpoints(role_str, expected, ctx):
    cluster_relation = Relation(
        "tempo-cluster",
        remote_app_data={
            "worker_config": json.dumps("some: yaml"),
        },
    )

    state = State(
        leader=True,
        containers=[tempo_container],
        relations=[cluster_relation],
    )

    with endpoint_ready(), config_on_disk():
        state_out = ctx.run(ctx.on.collect_unit_status(), set_role(state, role_str))
        assert state_out.unit_status == expected


@pytest.mark.parametrize(
    "roles_enabled",
    (
        (
            "all",
            "metrics-generator",
        ),
        (
            "ingester",
            "metrics-generator",
        ),
        (
            "ingester",
            "compactor",
            "querier",
        ),
    ),
)
def test_status_too_many_roles_enabled(roles_enabled, ctx):
    cluster_relation = Relation(
        "tempo-cluster",
        remote_app_data={
            "worker_config": json.dumps("some: yaml"),
        },
    )

    state = State(
        leader=True,
        config={f"role-{role}": True for role in roles_enabled},
        containers=[tempo_container],
        relations=[cluster_relation],
    )

    with endpoint_ready(), config_on_disk():
        state_out = ctx.run(ctx.on.collect_unit_status(), state)
        assert state_out.unit_status == BlockedStatus(
            f"cannot have more than 1 enabled role: {list(roles_enabled) + (['all'] if 'all' not in roles_enabled else [])}"
        )


def test_blocked_config_generator_no_config(ctx):
    cluster_relation = Relation(
        "tempo-cluster",
        remote_app_data={
            "worker_config": json.dumps("some: yaml"),
        },
    )

    state = State(
        leader=True,
        containers=[tempo_container],
        relations=[cluster_relation],
    )

    with endpoint_ready(), config_on_disk():
        state_out = ctx.run(
            ctx.on.collect_unit_status(), set_role(state, "metrics-generator")
        )

        assert state_out.unit_status == BlockedStatus(
            "No prometheus remote-write relation configured on the coordinator"
        )
