import asyncio
import json
import logging
from pathlib import Path

import pytest
import yaml
from helpers import WORKER_NAME, deploy_monolithic_cluster
from pytest_operator.plugin import OpsTest

from tests.integration.helpers import get_traces_patiently

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
TESTER_METADATA = yaml.safe_load(Path("./tests/integration/tester/metadata.yaml").read_text())
TESTER_APP_NAME = TESTER_METADATA["name"]
TESTER_GRPC_METADATA = yaml.safe_load(
    Path("./tests/integration/tester-grpc/metadata.yaml").read_text()
)
TESTER_GRPC_APP_NAME = TESTER_GRPC_METADATA["name"]

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_build_deploy_testers(ops_test: OpsTest, tempo_charm: Path):
    # Given a fresh build of the charm
    # When deploying it together with testers
    # Then applications should eventually be created
    tester_charm = await ops_test.build_charm("./tests/integration/tester/")
    tester_grpc_charm = await ops_test.build_charm("./tests/integration/tester-grpc/")
    resources = {
        "nginx-image": METADATA["resources"]["nginx-image"]["upstream-source"],
        "nginx-prometheus-exporter-image": METADATA["resources"][
            "nginx-prometheus-exporter-image"
        ]["upstream-source"],
    }
    resources_tester = {"workload": TESTER_METADATA["resources"]["workload"]["upstream-source"]}
    resources_tester_grpc = {
        "workload": TESTER_GRPC_METADATA["resources"]["workload"]["upstream-source"]
    }

    await asyncio.gather(
        ops_test.model.deploy(
            tempo_charm, resources=resources, application_name=APP_NAME, trust=True
        ),
        ops_test.model.deploy(
            tester_charm,
            resources=resources_tester,
            application_name=TESTER_APP_NAME,
            num_units=3,
        ),
        ops_test.model.deploy(
            tester_grpc_charm,
            resources=resources_tester_grpc,
            application_name=TESTER_GRPC_APP_NAME,
            num_units=3,
        ),
    )

    await deploy_monolithic_cluster(ops_test)

    await asyncio.gather(
        # for both testers, depending on the result of race with tempo it's either waiting or active
        ops_test.model.wait_for_idle(
            apps=[TESTER_APP_NAME, TESTER_GRPC_APP_NAME],
            raise_on_blocked=True,
            timeout=2000,
            raise_on_error=False,
        ),
    )

    assert ops_test.model.applications[APP_NAME].units[0].workload_status == "active"


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_relate(ops_test: OpsTest):
    # given a deployed charm
    # when relating it together with the tester
    # then relation should appear
    await ops_test.model.add_relation(APP_NAME + ":tracing", TESTER_APP_NAME + ":tracing")
    await ops_test.model.add_relation(APP_NAME + ":tracing", TESTER_GRPC_APP_NAME + ":tracing")
    async with ops_test.fast_forward():
        await ops_test.model.wait_for_idle(
            apps=[APP_NAME, WORKER_NAME, TESTER_APP_NAME, TESTER_GRPC_APP_NAME],
            status="active",
            timeout=1000,
        )


async def test_verify_traces_http(ops_test: OpsTest):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain traces from the tester charm
    status = await ops_test.model.get_status()
    app = status["applications"][APP_NAME]
    traces = await get_traces_patiently(
        tempo_host=app.public_address, service_name="TempoTesterCharm", tls=False
    )
    assert (
        traces
    ), f"There's no trace of charm exec traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.skip(reason="fails because search query results are not stable")
# keep an eye onhttps://github.com/grafana/tempo/issues/3777 and see if they fix it
async def test_verify_buffered_charm_traces_http(ops_test: OpsTest):
    # given a relation between charms
    # when traces endpoint is queried
    # then it should contain all traces from the tester charm since the setup phase, thanks to the buffer
    status = await ops_test.model.get_status()
    app = status["applications"][APP_NAME]
    traces = await get_traces_patiently(
        tempo_host=app.public_address, service_name="TempoTesterCharm", tls=False
    )

    # charm-tracing trace names are in the format:
    # "mycharm/0: <event-name> event"
    captured_events = {trace["rootTraceName"].split(" ")[1] for trace in traces}
    expected_setup_events = {
        "start",
        "install",
        "leader-elected",
        "tracing-relation-created",
        "replicas-relation-created",
    }
    assert expected_setup_events.issubset(captured_events)


async def test_verify_traces_grpc(ops_test: OpsTest):
    # the tester-grpc charm emits a single grpc trace in its common exit hook
    # we verify it's there
    status = await ops_test.model.get_status()
    app = status["applications"][APP_NAME]
    logger.info(app.public_address)
    traces = await get_traces_patiently(
        tempo_host=app.public_address, service_name="TempoTesterGrpcCharm", tls=False
    )
    assert (
        traces
    ), f"There's no trace of generated grpc traces in tempo. {json.dumps(traces, indent=2)}"


@pytest.mark.teardown
@pytest.mark.abort_on_fail
async def test_remove_relation(ops_test: OpsTest):
    # given related charms
    # when relation is removed
    # then both charms should become active again
    await ops_test.juju("remove-relation", APP_NAME + ":tracing", TESTER_APP_NAME + ":tracing")
    await ops_test.juju(
        "remove-relation", APP_NAME + ":tracing", TESTER_GRPC_APP_NAME + ":tracing"
    )
    await asyncio.gather(
        ops_test.model.wait_for_idle(
            apps=[APP_NAME], status="active", raise_on_blocked=True, timeout=1000
        ),
        # for tester, depending on the result of race with tempo it's either waiting or active
        ops_test.model.wait_for_idle(apps=[TESTER_APP_NAME], raise_on_blocked=True, timeout=1000),
        ops_test.model.wait_for_idle(
            apps=[TESTER_GRPC_APP_NAME], raise_on_blocked=True, timeout=1000
        ),
    )
