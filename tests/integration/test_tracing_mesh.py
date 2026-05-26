import time

import jubilant
import pytest
from jubilant import Juju

from tests.integration.helpers import (
    ISTIO_APP,
    ISTIO_BEACON_APP,
    S3_APP,
    TEMPO_APP,
    WORKER_APP,
    GRAFANA_APP,
    deploy_istio,
    deploy_istio_beacon,
    deploy_monolithic_cluster,
    deploy_grafana,
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
    deploy_grafana(juju)

    # Enable otlp_grpc so that the grpc ingestion endpoint is available even
    # without a requirer charm on the tracing relation requesting it.
    juju.config(TEMPO_APP, {"always_enable_otlp_grpc": "True"})

    # Integrate Prometheus with Tempo via both tracing endpoints.
    # charm-tracing (HTTP) and workload-tracing (gRPC) are added once here and
    # kept active for the duration of all mesh tests.
    juju.integrate(f"{GRAFANA_APP}:charm-tracing", f"{TEMPO_APP}:tracing")
    juju.integrate(f"{GRAFANA_APP}:workload-tracing", f"{TEMPO_APP}:tracing")

    # Single wait for the entire model to settle.
    juju.wait(
        lambda status: jubilant.all_active(
            status, ISTIO_APP, ISTIO_BEACON_APP, TEMPO_APP, WORKER_APP, S3_APP, GRAFANA_APP
        ),
        timeout=2000,
        delay=5,
        successes=5,
    )


def test_verify_traces(juju: Juju):
    # GIVEN a deployed tempo cluster enrolled in a service mesh,
    #       with Grafana related via both charm-tracing (HTTP) and
    #       workload-tracing (gRPC)
    # WHEN  Grafana emits spans into the mesh
    # THEN  they should appear in the tempo trace store
    with service_mesh(
        juju=juju,
        beacon_app_name=ISTIO_BEACON_APP,
        # Prometheus is auto-enrolled in ztunnel via the namespace ambient label;
        # it has no service-mesh Juju endpoint and doesn't need one.
        apps_to_be_related_with_beacon=[TEMPO_APP, GRAFANA_APP],
    ):
        start_time = time.time()

        juju.run(GRAFANA_APP + "/0", "get-admin-password")  # run an action in grafana to trigger a charm trace

        time.sleep(10)  # give the trace some time to be emitted and ingested before we start polling

        traces = query_traces_patiently_from_worker_pod(
            juju=juju,
            service_name=GRAFANA_APP,
            start_time=start_time,
        )
        assert traces, "expected traces from Grafana in the mesh, found none"
