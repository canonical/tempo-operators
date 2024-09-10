import json
from dataclasses import replace

import yaml
from scenario import State

from charm import TempoCoordinatorCharm


def test_memberlist_multiple_members(
    context, all_worker, s3, nginx_container, nginx_prometheus_exporter_container
):
    workers_no = 3
    all_worker = replace(
        all_worker,
        remote_units_data={
            worker_idx: {
                "address": json.dumps(f"worker-{worker_idx}.test.svc.cluster.local:7946"),
                "juju_topology": json.dumps(
                    {
                        "model": "test",
                        "unit": f"worker/{worker_idx}",
                        "model_uuid": "1",
                        "application": "worker",
                        "charm_name": "TempoWorker",
                    }
                ),
            }
            for worker_idx in range(workers_no)
        },
    )
    state = State(
        leader=True,
        relations=[all_worker, s3],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(all_worker), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        assert charm.coordinator.cluster.gather_addresses() == set(
            [
                "worker-0.test.svc.cluster.local:7946",
                "worker-1.test.svc.cluster.local:7946",
                "worker-2.test.svc.cluster.local:7946",
            ]
        )


def test_metrics_generator(
    context,
    all_worker,
    s3,
    nginx_container,
    nginx_prometheus_exporter_container,
    remote_write,
):
    state = State(
        leader=True,
        relations=[all_worker, s3],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(all_worker), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        config_raw = charm.tempo.config(charm.coordinator)
        config = yaml.safe_load(config_raw)
        assert "metrics_generator" not in config
        assert "overrides" not in config

    # add remote-write relation
    state = State(
        leader=True,
        relations=[all_worker, s3, remote_write],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    with context(context.on.relation_changed(remote_write), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        # assert charm.coordinator.cert_handler.server_cert
        config_raw = charm.tempo.config(charm.coordinator)
        config = yaml.safe_load(config_raw)
        assert "metrics_generator" in config
        assert config["metrics_generator"]["storage"]["remote_write"] == [
            json.loads(remote_write.remote_units_data[0]["remote_write"])
        ]
        assert "overrides" in config
