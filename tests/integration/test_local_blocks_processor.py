# Copyright 2026 Canonical Ltd.
# See LICENSE file for licensing details.
"""Integration tests for the local_blocks metrics-generator processor (PR #418)."""

import logging

import jubilant
import pytest
from jubilant import Juju
from tenacity import retry, stop_after_attempt, wait_fixed

from tests.integration.helpers import (
    PROMETHEUS_APP,
    S3_APP,
    TEMPO_APP,
    WORKER_APP,
    deploy_monolithic_cluster,
    deploy_prometheus,
    metric_value,
    scrape_metrics,
)

logger = logging.getLogger(__name__)


@pytest.mark.juju_setup
def test_deploy(juju: Juju):
    """Deploy a monolithic cluster with remote-write to enable the metrics-generator."""
    deploy_monolithic_cluster(juju, wait_for_idle=False)
    deploy_prometheus(juju)
    juju.integrate(
        f"{PROMETHEUS_APP}:receive-remote-write",
        f"{TEMPO_APP}:send-remote-write",
    )
    juju.wait(
        lambda status: jubilant.all_active(
            status, TEMPO_APP, WORKER_APP, S3_APP, PROMETHEUS_APP
        ),
        timeout=2000,
        delay=5,
        successes=3,
    )
    # speed up update-status hooks to generate self-traces faster
    juju.cli("model-config", "update-status-hook-interval=10s")


def test_local_blocks_processor_active(juju: Juju):
    # retry: metrics-generator pipeline is async
    @retry(stop=stop_after_attempt(12), wait=wait_fixed(10))
    def _check() -> None:
        metrics = scrape_metrics(juju, WORKER_APP)
        spans = metric_value(
            metrics, "tempo_metrics_generator_processor_local_blocks_spans_total"
        )
        assert spans > 0

    _check()


def test_local_blocks_wal_operational(juju: Juju):
    # live_trace_bytes > 0 proves traces_storage.path is set
    @retry(stop=stop_after_attempt(6), wait=wait_fixed(10))
    def _check() -> None:
        metrics = scrape_metrics(juju, WORKER_APP)
        live_bytes = metric_value(
            metrics,
            "tempo_metrics_generator_processor_local_blocks_live_trace_bytes",
        )
        assert live_bytes > 0

    _check()


def test_local_blocks_no_flush_errors(juju: Juju):
    metrics = scrape_metrics(juju, WORKER_APP)
    failed = metric_value(
        metrics, "tempo_metrics_generator_processor_local_blocks_failed_flushes_total"
    )
    assert failed == 0


@pytest.mark.juju_teardown
def test_teardown(juju: Juju):
    for app in (WORKER_APP, TEMPO_APP, S3_APP, PROMETHEUS_APP):
        if app in juju.status().apps:
            juju.remove_application(app)
