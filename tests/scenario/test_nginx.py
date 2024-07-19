import logging
from typing import List

import pytest

from nginx_config import NginxConfig
from tempo import Tempo

logger = logging.getLogger(__name__)


def test_nginx_config_is_list_before_crossplane(context, nginx_container, coordinator):
    nginx = NginxConfig("localhost")
    prepared_config = nginx._prepare_config(coordinator)
    assert isinstance(prepared_config, List)


def test_nginx_config_is_parsed_by_crossplane(context, nginx_container, coordinator):
    nginx = NginxConfig("localhost")
    prepared_config = nginx.config(coordinator)
    assert isinstance(prepared_config, str)


@pytest.mark.parametrize(
    "addresses",
    (
        {},
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5", "1.2.3.7"},
            "ingester": {"1.2.3.6", "1.2.3.8"},
            "querier": {"1.2.4.7", "1.2.4.9"},
            "query_frontend": {"1.2.5.1", "1.2.5.2"},
            "compactor": {"1.2.6.6", "1.2.6.7"},
            "metrics_generator": {"1.2.8.4", "1.2.8.5"},
        },
    ),
)
def test_nginx_config_is_parsed_with_workers(context, nginx_container, coordinator, addresses):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses

    nginx = NginxConfig("localhost")

    prepared_config = nginx.config(coordinator)
    assert isinstance(prepared_config, str)


@pytest.mark.parametrize(
    "addresses",
    (
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
        {
            "distributor": {"1.2.3.5"},
            "ingester": {"1.2.3.6"},
            "querier": {"1.2.4.7"},
            "query_frontend": {"1.2.5.1"},
            "compactor": {"1.2.6.6"},
            "metrics_generator": {"1.2.8.4"},
        },
    ),
)
def test_nginx_config_contains_upstreams_and_proxy_pass(
    context, nginx_container, coordinator, addresses
):
    coordinator.cluster.gather_addresses_by_role.return_value = addresses

    nginx = NginxConfig("localhost")

    prepared_config = nginx.config(coordinator)

    for role, addresses in addresses.items():
        for address in addresses:
            if role == "distributor":
                _assert_config_per_role(Tempo.receiver_ports, address, prepared_config)
            if role == "query-frontend":
                _assert_config_per_role(Tempo.server_ports, address, prepared_config)


def _assert_config_per_role(source_dict, address, prepared_config):
    # as entire config is in a format that's hard to parse (and crossplane returns a string), we look for servers,
    # upstreams and correct proxy/grpc_pass instructions.
    for port in source_dict.values():
        assert f"server {address}:{port};" in prepared_config
        assert f"listen {port}" in prepared_config
        assert f"listen [::]:{port}" in prepared_config
    for protocol in source_dict.keys():
        sanitised_protocol = protocol.replace("_", "-")
        assert f"upstream {sanitised_protocol}" in prepared_config
        if "grpc" in protocol:
            assert f"grpc_pass grpcs://{sanitised_protocol}" in prepared_config
        else:
            assert f"proxy_pass https://{sanitised_protocol}" in prepared_config
