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
        traces = query_traces_patiently_from_worker_pod(
            juju=juju,
            service_name=PROMETHEUS_APP,
            start_time=start_time,
        )
        assert traces, "expected traces from Prometheus in the mesh, found none"


def test_verify_traces_grpc(juju: Juju):
    # GIVEN a deployed tempo cluster enrolled in a service mesh with otlp_grpc enabled
    # WHEN Prometheus emits gRPC spans via workload-tracing during mesh enrolment
    # THEN they should appear in the tempo trace store
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
        assert traces, "expected traces from Prometheus in the mesh, found none"


def test_verify_tempo_api_integration(juju: Juju):
    # GIVEN a deployed tempo cluster enrolled in a service mesh
    # WHEN Prometheus emits HTTP spans via charm-tracing during mesh enrolment
    # THEN they should be accessible via the internal tempo API from within the cluster
    #
    # NOTE: the original version of this test used a dedicated tester charm that consumed the
    # tempo-api Juju relation and verified cross-app access. The tester charm was removed in
    # favour of Prometheus-generated traces. Proper tempo-api RBAC mesh testing needs a charm
    # with a tempo-api requirer (e.g. Grafana).
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
        assert traces, "expected traces from Prometheus via the tempo API in the mesh, found none"


def test_verify_grafana_datasource_integration(juju: Juju):
    # GIVEN a deployed tempo cluster enrolled in a service mesh
    # WHEN Prometheus emits gRPC spans via workload-tracing during mesh enrolment
    # THEN they should be accessible via the grafana-datasource endpoint from within the cluster
    #
    # NOTE: the original version of this test used a dedicated tester charm that consumed the
    # grafana-source Juju relation and verified cross-app access. The tester charm was removed
    # in favour of Prometheus-generated traces. Proper grafana-source RBAC mesh testing needs
    # a charm with a grafana-datasources requirer.
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
        assert traces, "expected traces from Prometheus via the grafana-datasource endpoint in the mesh, found none"
