import logging
import time

import jubilant
import pytest
from jubilant import Juju
from tenacity import retry, stop_after_attempt, wait_fixed

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
    query_traces_from_worker_pod,
    service_mesh,
)

logger = logging.getLogger(__name__)


@retry(stop=stop_after_attempt(20), wait=wait_fixed(20))
def _generate_traces_and_query(juju: Juju) -> list:
    # Run an action on Grafana to trigger a charm hook.  charm-tracing
    # flushes buffered spans at the end of every hook, so each attempt
    # gives the charm a fresh chance to push traces through the mesh.
    # This is essential because the Istio waypoint/ztunnel data plane
    # may not be fully converged when the first hooks fire, causing
    # those early flushes to fail (traces are preserved in a buffer on
    # the unit and retried on the next hook).
    juju.run(f"{GRAFANA_APP}/0", "get-admin-password")

    time.sleep(5)  # give the trace a moment to be ingested

    traces = query_traces_from_worker_pod(
        juju=juju,
        service_name=GRAFANA_APP,
    )

    if not traces:
        # Log available service names for diagnostics.
        try:
            result = juju.exec(
                'python3 -c "'
                "import urllib.request, json; "
                "r = urllib.request.urlopen('http://localhost:3200/api/search/tag/service.name/values'); "
                'print(r.read().decode())"',
                unit=f"{WORKER_APP}/0",
            )
            logger.warning(
                "no traces for service=%r; available service names: %s",
                GRAFANA_APP,
                result.stdout.strip(),
            )
        except Exception:
            pass  # best-effort diagnostics

    assert traces, "no traces found for Grafana in the mesh"
    return traces


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

    # Single wait for the entire model to settle.
    # NOTE: the Grafana ↔ Tempo *tracing* integrations are deliberately
    # deferred to test_verify_traces so that they are established only after
    # the service mesh is active.  This guarantees that every Grafana trace
    # found in Tempo must have traversed the mesh.
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
        # Establish the tracing relations only now that the mesh is active.
        # This ensures every Grafana trace in Tempo must have gone through
        # the mesh (ztunnel + waypoint).
        juju.integrate(f"{GRAFANA_APP}:charm-tracing", f"{TEMPO_APP}:tracing")
        juju.integrate(f"{GRAFANA_APP}:workload-tracing", f"{TEMPO_APP}:tracing")

        # Integrate Grafana datasource/dashboard to trigger additional charm hooks.
        juju.integrate(f"{GRAFANA_APP}:grafana-source", f"{TEMPO_APP}:grafana-source")
        juju.integrate(f"{GRAFANA_APP}:grafana-dashboard", f"{TEMPO_APP}:grafana-dashboard")

        traces = _generate_traces_and_query(juju)
        assert traces, "expected traces from Grafana in the mesh, found none"
