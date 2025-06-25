from subprocess import CalledProcessError

import jubilant
import pytest
from jubilant import Juju
from tenacity import RetryError

from tests.integration.helpers import (
    TEMPO_APP,
    S3_APP,
    protocols_endpoints,
    get_tempo_hostname,
    get_tempo_ingestion_endpoint,
)
from helpers import emit_trace, get_traces_patiently


def test_tempo_blocks_if_s3_goes_away(juju: Juju):
    # GIVEN a tempo cluster
    # WHEN s3-relation is removed
    juju.remove_relation(S3_APP, TEMPO_APP)
    # THEN tempo coordinator is in blocked state
    juju.wait(lambda status: jubilant.all_blocked(status, TEMPO_APP), timeout=1000)


@pytest.mark.parametrize("protocol", protocols_endpoints.keys())
def test_profiles_ingestion_fails(juju: Juju, protocol, tls, nonce, ingress):
    # GIVEN a tempo cluster with no s3 integrator and no workers
    # WHEN we emit a profile through Pyroscope's HTTP API server
    tempo_host = get_tempo_hostname(juju, ingress)
    tempo_endpoint = get_tempo_ingestion_endpoint(
        tempo_host,
        protocol=protocol,
        tls=tls,
    )

    # emit a trace
    service_name = f"tracegen{'-tls' if tls else ''}-{protocol}"
    with pytest.raises(CalledProcessError):
        emit_trace(
            tempo_endpoint,
            juju,
            nonce=nonce,
            verbose=1,
            proto=protocol,
            use_cert=tls,
            service_name=service_name,
        )


def test_profiles_query_fails(juju: Juju, protocol, tls, nonce, ingress):
    # GIVEN a tempo cluster with no s3 integrator and no workers
    # WHEN we emit a profile through Pyroscope's HTTP API server
    tempo_host = get_tempo_hostname(juju, ingress)
    # emit a trace
    service_name = f"tracegen{'-tls' if tls else ''}-{protocol}"
    with pytest.raises(RetryError):
        get_traces_patiently(
            tempo_host,
            nonce=nonce,
            tls=tls,
            service_name=service_name,
        )
