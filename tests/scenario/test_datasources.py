import json
from unittest.mock import patch

import scenario
from cosl.interfaces.datasource_exchange import (
    DatasourceExchange,
    DSExchangeAppData,
    GrafanaDatasource,
)
from scenario import PeerRelation, Relation, State


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

    mimir_dsx = Relation(
        "receive-datasource",
        remote_app_data=DSExchangeAppData(
            datasources=json.dumps(sorted(ds_mimir, key=lambda raw_ds: raw_ds["uid"]))
        ).dump(),
    )
    loki_dsx = Relation(
        "receive-datasource",
        remote_app_data=DSExchangeAppData(
            datasources=json.dumps(sorted(ds_loki, key=lambda raw_ds: raw_ds["uid"]))
        ).dump(),
    )

    ds = Relation(
        "grafana-source",
        remote_app_data={
            "grafana_uid": "foo-something-bars",
            "datasource_uids": json.dumps({"tempo/0": "1234"}),
        },
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
