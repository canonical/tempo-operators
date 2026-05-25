import time

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import (
    ISTIO_APP,
    ISTIO_BEACON_APP,
    PROMETHEUS_APP,
    TEMPO_APP,
    WORKER_APP,
    deploy_istio,
    deploy_istio_beacon,
    deploy_monolithic_cluster,
    deploy_prometheus,
    query_traces_patiently_from_worker_pod,
    service_mesh,
)


@pytest.mark.juju_setup
def test_deploy_istio(juju: Juju):
    # Deploy Istio control plane and ambient-mode beacon.
    deploy_istio(juju)
    deploy_istio_beacon(juju)
    juju.wait(
        lambda status: jubilant.all_active(status, ISTIO_APP, ISTIO_BEACON_APP),
        timeout=1000,
    )


@pytest.mark.juju_setup
def test_deploy_monolithic_cluster(juju: Juju):
    deploy_monolithic_cluster(juju)


@pytest.mark.juju_setup
# scaling the coordinator before ingesting traces to verify that scaling won't stop traces ingestion.
def test_scale_up_tempo(juju: Juju):
    # GIVEN we scale up tempo
    juju.add_unit(TEMPO_APP, num_units=2)
    # THEN all units become active
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP), timeout=1000
    )


@pytest.mark.juju_setup
def test_enable_otlp_grpc(juju: Juju):
    # Enable otlp_grpc so that the grpc ingestion tests can run.
    # otlp_http is always enabled; otlp_grpc needs to be explicitly force-enabled
    # when there is no requirer charm on the tracing relation requesting it.
    juju.config(TEMPO_APP, {"always_enable_otlp_grpc": "True"})
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, WORKER_APP),
        timeout=1000,
        delay=5,
        successes=3,
    )


@pytest.mark.juju_setup
def test_deploy_prometheus(juju: Juju):
    """Deploy Prometheus as the in-mesh tracing consumer."""
    if PROMETHEUS_APP not in juju.status().apps:
        deploy_prometheus(juju)
    juju.wait(
        lambda status: jubilant.all_active(status, PROMETHEUS_APP),
        timeout=600,
        delay=5,
        successes=3,
    )


@pytest.mark.juju_setup
def test_relate_prometheus(juju: Juju):
    """Integrate Prometheus with Tempo via both tracing endpoints.

    charm-tracing (HTTP) and workload-tracing (gRPC) are added once here and
    kept active for the duration of all mesh tests.  Per-test add/remove is not
    needed because query_traces_patiently_from_worker_pod already filters by a
    start_time window, so traces from a previous test never pollute the next one.
    """
    juju.integrate(f"{PROMETHEUS_APP}:charm-tracing", f"{TEMPO_APP}:tracing")
    juju.integrate(f"{PROMETHEUS_APP}:workload-tracing", f"{TEMPO_APP}:tracing")
    juju.wait(
        lambda status: jubilant.all_active(status, TEMPO_APP, PROMETHEUS_APP),
        timeout=300,
        delay=5,
        successes=3,
    )


@pytest.mark.juju_setup
def test_configure_prometheus_eval_interval(juju: Juju):
    """Shorten Prometheus evaluation interval to generate workload-tracing spans more often.

    At the default 1-minute evaluation interval only ~6 gRPC workload-tracing cycles fit inside
    the ~400 s retry window of query_traces_patiently_from_worker_pod.  15 s gives ~26 cycles,
    making test_verify_traces_grpc reliably fast without changing the retry window.
    """
    juju.config(PROMETHEUS_APP, {"evaluation_interval": "15s"})
    juju.wait(
        lambda status: jubilant.all_active(status, PROMETHEUS_APP),
        timeout=300,
        delay=5,
        successes=3,
    )


def test_verify_traces_http(juju: Juju):
    # GIVEN a deployed tempo cluster enrolled in a service mesh
    # WHEN Prometheus emits HTTP spans via charm-tracing during mesh enrolment
    # THEN they should appear in the tempo trace store
    start_time = int(time.time())
    with service_mesh(
        juju=juju,
        beacon_app_name=ISTIO_BEACON_APP,
        # Prometheus is auto-enrolled in ztunnel via the namespace ambient label;
        # it has no service-mesh Juju endpoint and doesn't need one.
        apps_to_be_related_with_beacon=[TEMPO_APP],
    ):
        # Trigger a config-changed hook on Prometheus so that charm-tracing emits an HTTP span.
        # Without an explicit hook, no Prometheus Juju events fire during mesh enrolment, so no
        # charm-tracing spans are generated after start_time.
        juju.config(PROMETHEUS_APP, {"log_level": "debug"})
        juju.wait(
            lambda status: jubilant.all_active(status, PROMETHEUS_APP),
            timeout=300,
            delay=5,
            successes=3,
        )
        juju.config(PROMETHEUS_APP, {"log_level": ""})  # reset to charm default
        traces = query_traces_patiently_from_worker_pod(
            juju=juju,
            service_name=PROMETHEUS_APP,
            start_time=start_time,
        )
        assert traces, "expected HTTP charm-tracing spans from Prometheus in the mesh, found none"


def test_verify_traces_grpc(juju: Juju):
    # GIVEN a deployed tempo cluster enrolled in a service mesh with otlp_grpc enabled
    # WHEN Prometheus emits gRPC spans via workload-tracing during mesh enrolment
    # THEN they should appear in the tempo trace store
    #
    # The Prometheus binary sends OTLP gRPC spans every evaluation-interval (set to 15 s in
    # test_configure_prometheus_eval_interval).  No explicit hook trigger is needed:
    # query_traces_patiently_from_worker_pod retries for ~400 s, during which Prometheus
    # produces ~26 evaluation cycles worth of workload-tracing spans.
    start_time = int(time.time())
    with service_mesh(
        juju=juju,
        beacon_app_name=ISTIO_BEACON_APP,
        # Prometheus is auto-enrolled in ztunnel via the namespace ambient label;
        # it has no service-mesh Juju endpoint and doesn't need one.
        apps_to_be_related_with_beacon=[TEMPO_APP],
    ):
        traces = query_traces_patiently_from_worker_pod(
            juju=juju,
            service_name=PROMETHEUS_APP,
            start_time=start_time,
        )
        assert traces, "expected gRPC workload-tracing spans from Prometheus in the mesh, found none"

