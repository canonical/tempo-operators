from unittest.mock import patch

import pytest

# wokeignore:rule=blackbox
from charms.blackbox_exporter_k8s.v0.blackbox_probes import ApplicationDataModel
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from scenario import Relation, State


@pytest.fixture(autouse=True, scope="module")
def disable_charm_tracing():
    with charm_tracing_disabled():
        yield


@patch("socket.getfqdn", lambda: "1.2.3.4")
def test_probes_contain_correct_url(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):

    probes = Relation("probes")

    state = State(
        leader=True,
        relations=[s3, all_worker, probes],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(probes), state) as mgr:
        out = mgr.run()

    probes_out = out.get_relations(probes.endpoint)[0]
    local_app_data = ApplicationDataModel.load(probes_out.local_app_data)

    assert (
        local_app_data.scrape_probes[0].static_configs[0].targets[0] == "http://1.2.3.4:3200/ready"
    )


@patch("socket.getfqdn", lambda: "1.2.3.4")
def test_probes_contain_correct_url_with_ingress(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):

    probes = Relation("probes")

    ingress = Relation("ingress", remote_app_data={"external_host": "app.py", "scheme": "http"})

    state = State(
        leader=True,
        relations=[s3, all_worker, probes, ingress],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(probes), state) as mgr:
        out = mgr.run()

    probes_out = out.get_relations(probes.endpoint)[0]
    local_app_data = ApplicationDataModel.load(probes_out.local_app_data)

    assert (
        local_app_data.scrape_probes[0].static_configs[0].targets[0] == "http://app.py:3200/ready"
    )


@patch("socket.getfqdn", lambda: "1.2.3.4")
def test_probes_contain_correct_url_with_ingress_on_https(
    context, s3, all_worker, nginx_container, nginx_prometheus_exporter_container
):

    probes = Relation("probes")

    ingress = Relation("ingress", remote_app_data={"external_host": "app.py", "scheme": "https"})

    state = State(
        leader=True,
        relations=[s3, all_worker, probes, ingress],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(probes), state) as mgr:
        out = mgr.run()

    probes_out = out.get_relations(probes.endpoint)[0]
    local_app_data = ApplicationDataModel.load(probes_out.local_app_data)

    assert (
        local_app_data.scrape_probes[0].static_configs[0].targets[0] == "https://app.py:3200/ready"
    )
