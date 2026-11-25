import json
import logging
from dataclasses import replace

import pytest
import yaml
from scenario import State

from charm import TempoCoordinatorCharm
from tests.unit.helpers import get_tempo_config

# Tempo's own default ingestion limits, which are also the charm's defaults.
DEFAULT_INGESTION = {"rate_limit_bytes": 15000000, "max_traces_per_user": 10000}


def test_memberlist_multiple_members(
    context, all_worker, s3, nginx_container, nginx_prometheus_exporter_container
):
    workers_no = 3
    all_worker = replace(
        all_worker,
        remote_units_data={
            worker_idx: {
                "address": json.dumps(
                    f"worker-{worker_idx}.test.svc.cluster.local:7946"
                ),
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
        assert charm.coordinator.cluster.gather_addresses() == tuple(
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
        assert "metrics_generator" not in config["overrides"]["defaults"]

    # add remote-write relation
    state = State(
        leader=True,
        relations=[all_worker, s3, remote_write],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )

    with context(context.on.relation_changed(remote_write), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        config_raw = charm.tempo.config(charm.coordinator)
        config = yaml.safe_load(config_raw)
        assert "metrics_generator" in config
        assert config["metrics_generator"]["storage"]["remote_write"] == [
            json.loads(remote_write.remote_units_data[0]["remote_write"])
        ]
        assert config["metrics_generator"]["processor"]["local_blocks"] == {
            "filter_server_spans": False
        }
        assert "overrides" in config
        assert config["overrides"]["defaults"]["metrics_generator"]["processors"] == [
            "span-metrics",
            "service-graphs",
            "local-blocks",
        ]
        assert config["overrides"]["defaults"]["ingestion"] == DEFAULT_INGESTION


def test_reporting_enabled_by_default(
    context,
    all_worker,
    s3,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    """When reporting_enabled is True (default), no usage_report section should appear."""
    state = State(
        leader=True,
        relations=[all_worker, s3],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )
    with context(context.on.relation_changed(all_worker), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        config = yaml.safe_load(charm.tempo.config(charm.coordinator))
        assert "usage_report" not in config


def test_reporting_disabled(
    context,
    all_worker,
    s3,
    nginx_container,
    nginx_prometheus_exporter_container,
):
    """When reporting_enabled is False, usage_report.reporting_enabled should be False."""
    state = State(
        leader=True,
        relations=[all_worker, s3],
        containers=[nginx_container, nginx_prometheus_exporter_container],
        config={"reporting_enabled": False},
    )
    with context(context.on.relation_changed(all_worker), state) as mgr:
        charm: TempoCoordinatorCharm = mgr.charm
        config = yaml.safe_load(charm.tempo.config(charm.coordinator))
        assert config["usage_report"] == {"reporting_enabled": False}


@pytest.fixture
def run_with_config(
    context, all_worker, s3, peer, nginx_container, nginx_prometheus_exporter_container
):
    """Run the charm with the given charm config and return the output state."""

    def _run(config):
        state = State(
            leader=True,
            config=config,
            relations=[peer, s3, all_worker],
            containers=[nginx_container, nginx_prometheus_exporter_container],
        )
        return context.run(context.on.relation_changed(all_worker), state)

    return _run


def _tempo_config(state_out):
    return yaml.safe_load(get_tempo_config(state_out))


@pytest.mark.parametrize(
    "config, expected_ingestion",
    (
        # no config set: the charm writes its defaults, which are Tempo's own defaults
        ({}, DEFAULT_INGESTION),
        # custom values are written as they are
        (
            {"ingestion_rate_limit_bytes": 2000000, "max_traces_per_user": 500},
            {"rate_limit_bytes": 2000000, "max_traces_per_user": 500},
        ),
        # 0 disables the live traces limit, so it is written too
        ({"max_traces_per_user": 0}, {**DEFAULT_INGESTION, "max_traces_per_user": 0}),
    ),
)
def test_ingestion_limits(run_with_config, config, expected_ingestion):
    state_out = run_with_config(config)
    overrides = _tempo_config(state_out)["overrides"]
    assert overrides["defaults"] == {"ingestion": expected_ingestion}
    assert state_out.unit_status.name == "active"


@pytest.mark.parametrize(
    "option, value, expected_ingestion",
    (
        ("ingestion_rate_limit_bytes", 0, {"max_traces_per_user": 10000}),
        ("ingestion_rate_limit_bytes", -1, {"max_traces_per_user": 10000}),
        ("max_traces_per_user", -1, {"rate_limit_bytes": 15000000}),
    ),
)
def test_ingestion_limits_invalid_value_is_ignored(
    run_with_config, caplog, option, value, expected_ingestion
):
    with caplog.at_level(logging.WARNING, logger="charm"):
        state_out = run_with_config({option: value})
    # the bad limit is left out, so Tempo uses its own default for it
    overrides = _tempo_config(state_out)["overrides"]
    assert overrides["defaults"] == {"ingestion": expected_ingestion}
    assert state_out.unit_status.name == "active"
    assert f"ignoring {option}={value}" in caplog.text


def test_ingestion_limits_all_invalid_values_are_ignored(run_with_config, caplog):
    config = {"ingestion_rate_limit_bytes": 0, "max_traces_per_user": -1}
    with caplog.at_level(logging.WARNING, logger="charm"):
        state_out = run_with_config(config)
    # nothing is left to write, so Tempo uses all its defaults
    assert "overrides" not in _tempo_config(state_out)
    assert state_out.unit_status.name == "active"
    assert "ignoring ingestion_rate_limit_bytes=0" in caplog.text
    assert "ignoring max_traces_per_user=-1" in caplog.text
