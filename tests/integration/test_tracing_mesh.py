import time

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import (
    ISTIO_APP,
    ISTIO_BEACON_APP,
    PROMETHEUS_APP,
    S3_APP,
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
def test_setup(juju: Juju):
    """Deploy and configure the full tracing + mesh test environment.

    All deployments, integrations, and config changes are issued without
    intermediate waits.  A single wait at the end settles the whole model.
    """
    deploy_istio(juju)
    deploy_istio_beacon(juju)
    deploy_monolithic_cluster(juju, wait_for_idle=False)

    if PROMETHEUS_APP not in juju.status().apps:
        deploy_prometheus(juju)

    # Enable otlp_grpc so that the grpc ingestion endpoint is available even
    # without a requirer charm on the tracing relation requesting it.
    juju.config(TEMPO_APP, {"always_enable_otlp_grpc": "True"})

    # Integrate Prometheus with Tempo via both tracing endpoints.
    # charm-tracing (HTTP) and workload-tracing (gRPC) are added once here and
    # kept active for the duration of all mesh tests.
    juju.integrate(f"{PROMETHEUS_APP}:charm-tracing", f"{TEMPO_APP}:tracing")
    juju.integrate(f"{PROMETHEUS_APP}:workload-tracing", f"{TEMPO_APP}:tracing")

    # Shorten Prometheus evaluation interval so workload-tracing gRPC spans are
    # emitted frequently enough for reliable testing.
    juju.config(PROMETHEUS_APP, {"evaluation_interval": "15s"})

    # Single wait for the entire model to settle.
    juju.wait(
        lambda status: jubilant.all_active(
            status, ISTIO_APP, ISTIO_BEACON_APP, TEMPO_APP, WORKER_APP, S3_APP, PROMETHEUS_APP
        ),
        timeout=2000,
        delay=5,
        successes=5,
    )


def test_verify_traces(juju: Juju):
    # GIVEN a deployed tempo cluster enrolled in a service mesh,
    #       with Prometheus related via both charm-tracing (HTTP) and
    #       workload-tracing (gRPC)
    # WHEN  Prometheus emits spans into the mesh
    # THEN  they should appear in the tempo trace store
    with service_mesh(
        juju=juju,
        beacon_app_name=ISTIO_BEACON_APP,
        # Prometheus is auto-enrolled in ztunnel via the namespace ambient label;
        # it has no service-mesh Juju endpoint and doesn't need one.
        apps_to_be_related_with_beacon=[TEMPO_APP],
    ):
        # Record start time after the mesh is fully set up so we only look for
        # spans emitted while the mesh is active.
        start_time = int(time.time())

        # Trigger a config-changed hook on Prometheus so that charm-tracing
        # emits an HTTP span.  Without an explicit hook, no Prometheus Juju
        # events fire during mesh enrolment, so no charm-tracing spans are
        # guaranteed after start_time.  The Prometheus binary also sends OTLP
        # gRPC workload-tracing spans every evaluation_interval (15 s), so both
        # protocols are exercised within a single mesh context.
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
        assert traces, "expected traces from Prometheus in the mesh, found none"
