import socket
from dataclasses import replace
from unittest.mock import patch

import pytest
from charms.tempo_coordinator_k8s.v0.charm_tracing import charm_tracing_disabled
from charms.tempo_coordinator_k8s.v0.tracing import (
    TracingProviderAppData,
    TracingRequirerAppData,
)
from scenario import Relation, Secret, State

from charm import TempoCoordinatorCharm


@pytest.fixture
def base_state(nginx_container, nginx_prometheus_exporter_container):
    return State(
        leader=True,
        secrets=[
            # secret used by certhandler's vault. We don't need it
            # if we're also simulating cert events before.
            Secret(
                "secret:chpv-test",
                label="cert-handler-private-vault",
                owner="app",
                latest_content={"foo": "bar"},
            )
        ],
        containers=[nginx_container, nginx_prometheus_exporter_container],
    )


def update_relations_tls_and_verify(
    base_state,
    context,
    has_ingress,
    local_has_tls,
    local_scheme,
    relations,
    remote_scheme,
    tracing,
):
    state = replace(base_state, relations=relations)
    with charm_tracing_disabled(), patch.object(
        TempoCoordinatorCharm, "are_certificates_on_disk", local_has_tls
    ):
        out = context.run(context.on.relation_changed(tracing), state)
    tracing_provider_app_data = TracingProviderAppData.load(
        out.get_relations(tracing.endpoint)[0].local_app_data
    )
    for receiver in tracing_provider_app_data.receivers:
        if receiver.protocol.name == "otlp_http":
            actual_url = receiver.url
    expected_url = f"{remote_scheme if has_ingress else local_scheme}://{socket.getfqdn() if not has_ingress else 'foo.com.org'}:4318"
    assert actual_url == expected_url
    return out


@pytest.mark.parametrize("remote_has_tls", (True, False))
@pytest.mark.parametrize("local_has_tls", (True, False))
@pytest.mark.parametrize("has_ingress", (True, False))
def test_tracing_endpoints_with_tls(
    context, base_state, s3, all_worker, has_ingress, local_has_tls, remote_has_tls
):
    tracing = Relation(
        "tracing",
        remote_app_data=TracingRequirerAppData(receivers=["otlp_http"]).dump(),
    )
    relations = [tracing, s3, all_worker]

    local_scheme = "https" if local_has_tls else "http"
    remote_scheme = "https" if remote_has_tls else "http"

    if has_ingress:
        relations.append(
            Relation(
                "ingress",
                remote_app_data={"scheme": remote_scheme, "external_host": "foo.com.org"},
            )
        )

    update_relations_tls_and_verify(
        base_state,
        context,
        has_ingress,
        local_has_tls,
        local_scheme,
        relations,
        remote_scheme,
        tracing,
    )


@pytest.mark.parametrize("has_ingress", (True, False))
def test_tracing_endpoints_tls_added_then_removed(
    context, s3, all_worker, base_state, has_ingress
):
    tracing = Relation(
        "tracing",
        remote_app_data=TracingRequirerAppData(receivers=["otlp_http"]).dump(),
    )
    relations = [tracing, s3, all_worker]

    local_scheme = "http"
    remote_scheme = "http"

    if has_ingress:
        relations.append(
            Relation(
                "ingress",
                remote_app_data={"scheme": remote_scheme, "external_host": "foo.com.org"},
            )
        )

    result_state = update_relations_tls_and_verify(
        base_state, context, has_ingress, False, local_scheme, relations, remote_scheme, tracing
    )

    # then we check the scenario where TLS gets enabled

    local_scheme = "https"
    remote_scheme = "https"

    if has_ingress:
        # as remote_scheme changed, we need to update the ingress relation
        relations.pop()
        relations.append(
            Relation(
                "ingress",
                remote_app_data={"scheme": remote_scheme, "external_host": "foo.com.org"},
            )
        )

    result_state = update_relations_tls_and_verify(
        result_state, context, has_ingress, True, local_scheme, relations, remote_scheme, tracing
    )

    # then we again remove TLS and compare the same thing

    local_scheme = "http"
    remote_scheme = "http"

    if has_ingress:
        # as remote_scheme changed, we need to update the ingress relation
        relations.pop()
        relations.append(
            Relation(
                "ingress",
                remote_app_data={"scheme": remote_scheme, "external_host": "foo.com.org"},
            )
        )

    update_relations_tls_and_verify(
        result_state, context, has_ingress, False, local_scheme, relations, remote_scheme, tracing
    )
