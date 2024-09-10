import logging
from pathlib import Path

import pytest
import yaml
from helpers import deploy_cluster
from juju.application import Application
from pytest_operator.plugin import OpsTest

METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())
APP_NAME = "tempo"
S3_INTEGRATOR = "s3-integrator"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

logger = logging.getLogger(__name__)


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_deploy_tempo(ops_test: OpsTest, tempo_charm):
    resources = {
        "nginx-image": METADATA["resources"]["nginx-image"]["upstream-source"],
        "nginx-prometheus-exporter-image": METADATA["resources"][
            "nginx-prometheus-exporter-image"
        ]["upstream-source"],
    }
    await ops_test.model.deploy(
        tempo_charm, resources=resources, application_name=APP_NAME, trust=True
    )

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        # coordinator will be blocked on s3 and workers integration
        status="blocked",
        timeout=10000,
        raise_on_error=False,
    )


@pytest.mark.abort_on_fail
async def test_scale_tempo_up_without_s3_blocks(ops_test: OpsTest):
    app: Application = ops_test.model.applications[APP_NAME]
    await app.add_unit(1)

    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        timeout=1000,
    )


@pytest.mark.setup
@pytest.mark.abort_on_fail
async def test_tempo_active_when_deploy_s3_and_workers(ops_test: OpsTest):
    await deploy_cluster(ops_test)


@pytest.mark.teardown
async def test_tempo_blocks_if_s3_goes_away(ops_test: OpsTest):
    app: Application = ops_test.model.applications[S3_INTEGRATOR]
    await app.destroy(destroy_storage=True)
    await ops_test.model.wait_for_idle(
        apps=[APP_NAME],
        status="blocked",
        timeout=1000,
    )
