import json
from typing import Dict, List
from unittest.mock import patch

import pytest
import scenario
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from cosl.interfaces.datasource_exchange import DatasourceExchange, GrafanaDatasource
from scenario import PeerRelation, Relation, State


@pytest.fixture(autouse=True, scope="module")
def disable_charm_tracing():
    with charm_tracing_disabled():
        yield


def grafana_source_relation(
    remote_name: str = "remote",
    datasource_uids: Dict[str, str] = {"tempo/0": "1234"},
    grafana_uid: str = "grafana_1",
):
    return Relation(
        "grafana-source",
        remote_app_name=remote_name,
        remote_app_data={
            "datasource_uids": json.dumps(datasource_uids),
            "grafana_uid": grafana_uid,
        },
    )


def grafana_datasource_exchange_relation(
    remote_name: str = "remote",
    datasources: List[Dict[str, str]] = [
        {"type": "prometheus", "uid": "prometheus_1", "grafana_uid": "grafana_1"}
    ],
):
    return Relation(
        "receive-datasource",
        remote_app_name=remote_name,
        remote_app_data={"datasources": json.dumps(datasources)},
    )


def remote_write_relation(
    remote_name: str = "remote",
    remote_write_data: Dict[str, str] = {"url": "http://prometheus:3000/api/write"},
):
    return Relation(
        "send-remote-write",
        remote_app_name=remote_name,
        remote_units_data={0: {"remote_write": json.dumps(remote_write_data)}},
    )


@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_datasource_receive(
    workload_ready_mock,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # GIVEN a regular HA deployment and two ds_exchange integrations with a mimir and a loki
    ds_loki = [
        {"type": "loki", "uid": "3", "grafana_uid": "4"},
    ]

    ds_mimir = [
        {"type": "prometheus", "uid": "8", "grafana_uid": "9"},
    ]

    mimir_dsx = grafana_datasource_exchange_relation(
        datasources=sorted(ds_mimir, key=lambda raw_ds: raw_ds["uid"])
    )
    loki_dsx = grafana_datasource_exchange_relation(
        datasources=sorted(ds_loki, key=lambda raw_ds: raw_ds["uid"])
    )

    ds = grafana_source_relation(
        datasource_uids={"tempo/0": "1234"}, grafana_uid="foo-something-bars"
    )

    state_in = State(
        relations=[
            PeerRelation("peers", peers_data={1: {}, 2: {}}),
            s3,
            all_worker,
            ds,
            mimir_dsx,
            loki_dsx,
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        unit_status=scenario.ActiveStatus(),
        leader=True,
    )

    # WHEN we receive any event
    with context(context.on.update_status(), state_in) as mgr:
        charm = mgr.charm
        # THEN we can find all received datasource uids in the coordinator
        dsx: DatasourceExchange = charm.coordinator.datasource_exchange
        received = dsx.received_datasources
        assert received == (
            GrafanaDatasource(type="loki", uid="3", grafana_uid="4"),
            GrafanaDatasource(type="prometheus", uid="8", grafana_uid="9"),
        )
        state_out = mgr.run()

    # AND THEN we forward our own datasource information to mimir and loki
    assert state_out.unit_status.name == "active"
    published_dsx_mimir = state_out.get_relation(mimir_dsx.id).local_app_data
    published_dsx_loki = state_out.get_relation(loki_dsx.id).local_app_data
    assert published_dsx_loki == published_dsx_mimir
    assert json.loads(published_dsx_loki["datasources"])[0] == {
        "type": "tempo",
        "uid": "1234",
        "grafana_uid": "foo-something-bars",
    }


@pytest.mark.parametrize("has_dsx", (True, False))
@pytest.mark.parametrize("has_remote_write", (True, False))
@pytest.mark.parametrize("has_grafana_source", (True, False))
@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_service_graph_with_complete_or_missing_rels(
    workload_ready_mock,
    has_grafana_source,
    has_remote_write,
    has_dsx,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):

    relations = [
        PeerRelation("peers", peers_data={1: {}, 2: {}}),
        s3,
        all_worker,
    ]

    if has_grafana_source:
        relations.append(grafana_source_relation())
    if has_dsx:
        relations.append(grafana_datasource_exchange_relation())
    if has_remote_write:
        relations.append(remote_write_relation())

    state_in = State(
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )

    # WHEN we fire any event
    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        service_graph_config = charm._build_service_graph_config()

        # If all relations are there
        # THEN assert that prometheus ds UID gets populated in the service graph config
        if all((has_dsx, has_grafana_source, has_remote_write)):
            assert service_graph_config
            assert service_graph_config["serviceMap"]["datasourceUid"] == "prometheus_1"
        # THEN no service graph config will be generated
        else:
            assert not service_graph_config


@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_no_service_graph_with_wrong_grafana(
    workload_ready_mock,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # WHEN tempo is connected as a datasource to multiple grafana instances
    # AND tempo is connected to multiple "prometheis" instances over datasource exchange
    # BUT the prometheus instance tempo is connected to over send-remote-write
    #  is a datasource for a different grafana instance
    relations = [
        PeerRelation("peers", peers_data={1: {}, 2: {}}),
        s3,
        all_worker,
        grafana_source_relation(remote_name="grafana1", grafana_uid="grafana_1"),
        grafana_source_relation(remote_name="grafana2", grafana_uid="grafana_2"),
        grafana_datasource_exchange_relation(
            remote_name="prometheus1",
            datasources=[
                {"type": "prometheus", "uid": "prometheus_1", "grafana_uid": "grafana_1"}
            ],
        ),
        grafana_datasource_exchange_relation(
            remote_name="prometheus2",
            datasources=[
                {"type": "prometheus", "uid": "prometheus_2", "grafana_uid": "grafana_3"}
            ],
        ),
        remote_write_relation(remote_name="prometheus2"),
    ]
    state_in = State(
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )

    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        service_graph_config = charm._build_service_graph_config()
        # THEN no service graph config should be generated.
        assert not service_graph_config


@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_service_graph_with_multiple_apps_and_units(
    workload_ready_mock,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # WHEN tempo is connected to multiple grafanas
    # AND tempo is connected to a multi-unit prometheus over send-remote-write
    # AND tempo is connected to prometheus over dsx
    # AND prometheus is connected to a grafana instance that is common with tempo
    relations = [
        PeerRelation("peers", peers_data={1: {}, 2: {}}),
        s3,
        all_worker,
        grafana_source_relation(remote_name="grafana1", grafana_uid="grafana_1"),
        grafana_source_relation(remote_name="grafana2", grafana_uid="grafana_2"),
        grafana_datasource_exchange_relation(
            remote_name="prometheus1",
            datasources=[
                {"type": "prometheus", "uid": "prometheus_1", "grafana_uid": "grafana_1"},
                {"type": "prometheus", "uid": "prometheus_2", "grafana_uid": "grafana_1"},
            ],
        ),
        remote_write_relation(remote_name="prometheus1"),
    ]
    state_in = State(
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )

    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        service_graph_config = charm._build_service_graph_config()
        # THEN a service graph config will be generated containing the datasource UID of any of the 2 prometheis units.
        assert service_graph_config["serviceMap"]["datasourceUid"] in [
            "prometheus_1",
            "prometheus_2",
        ]


@patch("charm.TempoCoordinatorCharm.is_workload_ready", return_value=True)
def test_no_service_graph_with_wrong_dsx(
    workload_ready_mock,
    context,
    s3,
    all_worker,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    # WHEN tempo is connected to a grafana instance
    # AND is connected to prometheus over send-remote-write
    # AND is connected to loki over dsx
    # BUT is not connected to prometheus over dsx
    relations = [
        PeerRelation("peers", peers_data={1: {}, 2: {}}),
        s3,
        all_worker,
        grafana_source_relation(remote_name="grafana1", grafana_uid="grafana_1"),
        grafana_datasource_exchange_relation(
            remote_name="loki1",
            datasources=[
                {"type": "loki", "uid": "loki_1", "grafana_uid": "grafana_1"},
            ],
        ),
        remote_write_relation(remote_name="prometheus1"),
    ]
    state_in = State(
        relations=relations,
        containers=[nginx_container, nginx_prometheus_exporter_container],
        leader=True,
    )

    with context(context.on.update_status(), state_in) as mgr:
        mgr.run()
        charm = mgr.charm
        service_graph_config = charm._build_service_graph_config()
        # THEN a service graph config will not be generated.
        assert not service_graph_config
