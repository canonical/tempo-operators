from itertools import chain

import ops
import pytest
from tempo_cluster import (
    TempoClusterProvider,
    TempoClusterRequirerAppData,
    TempoClusterRequirerUnitData,
    TempoRole,
)
from ops import Framework
from scenario import Context, Relation, State


class MyCharm(ops.CharmBase):
    META = {
        "name": "lukasz",
        "requires": {"tempo-cluster-require": {"interface": "tempo_cluster"}},
        "provides": {"tempo-cluster-provide": {"interface": "tempo_cluster"}},
    }

    def __init__(self, framework: Framework):
        super().__init__(framework)
        self.provider = TempoClusterProvider(self, endpoint="tempo-cluster-provide")


@pytest.mark.parametrize(
    "workers_roles, expected",
    (
        (
            (({TempoRole.overrides_exporter}, 1), ({TempoRole.overrides_exporter}, 1)),
            ({TempoRole.overrides_exporter: 2}),
        ),
        (
            (({TempoRole.query_frontend}, 1), ({TempoRole.overrides_exporter}, 1)),
            ({TempoRole.overrides_exporter: 1, TempoRole.query_frontend: 1}),
        ),
        ((({TempoRole.querier}, 2), ({TempoRole.querier}, 1)), ({TempoRole.querier: 3})),
        (
            (
                ({TempoRole.alertmanager}, 2),
                ({TempoRole.alertmanager}, 2),
                ({TempoRole.alertmanager, TempoRole.querier}, 1),
            ),
            ({TempoRole.alertmanager: 5, TempoRole.querier: 1}),
        ),
    ),
)
def test_role_collection(workers_roles, expected):
    relations = []
    for worker_roles, scale in workers_roles:
        data = TempoClusterRequirerAppData(roles=worker_roles).dump()
        relations.append(
            Relation(
                "tempo-cluster-provide",
                remote_app_data=data,
                remote_units_data={i: {} for i in range(scale)},
            )
        )

    state = State(relations=relations)

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        assert charm.provider.gather_roles() == expected


@pytest.mark.parametrize(
    "workers_addresses",
    (
        (("https://foo.com", "http://bar.org:8001"), ("https://bar.baz",)),
        (("//foo.com", "http://bar.org:8001"), ("foo.org:5000/noz",)),
        (
            ("https://foo.com:1", "http://bar.org:8001", "ohmysod"),
            ("u.buntu", "red-hat-chili-pepperz"),
            ("hoo.kah",),
        ),
    ),
)
def test_address_collection(workers_addresses):
    relations = []
    topo = {"unit": "foo/0", "model": "bar"}
    remote_app_data = TempoClusterRequirerAppData(roles=[TempoRole.alertmanager]).dump()
    for worker_addresses in workers_addresses:
        units_data = {
            i: TempoClusterRequirerUnitData(address=address, juju_topology=topo).dump()
            for i, address in enumerate(worker_addresses)
        }
        relations.append(
            Relation(
                "tempo-cluster-provide",
                remote_units_data=units_data,
                remote_app_data=remote_app_data,
            )
        )

    # all unit addresses should show up
    expected = set(chain(*workers_addresses))

    state = State(relations=relations)

    ctx = Context(MyCharm, meta=MyCharm.META)
    with ctx.manager("start", state) as mgr:
        mgr.run()
        charm: MyCharm = mgr.charm
        assert charm.provider.gather_addresses() == expected
