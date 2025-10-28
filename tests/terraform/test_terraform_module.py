# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import json
import jubilant
import pathlib
import pytest
import requests
import shlex
import subprocess

from tenacity import retry, stop_after_attempt, wait_fixed

from pytest_bdd import given, when, then


THIS_DIRECTORY = pathlib.Path(__file__).parent.resolve()
CHARM_CHANNEL = "2/edge"


def get_unit_ip_address(juju: jubilant.Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address


@pytest.fixture(scope="module")
def juju():
    with jubilant.temp_model() as tm:
        yield tm


@pytest.fixture(scope="module")
def seaweedfs(juju):
    juju.deploy("seaweedfs-k8s", "seaweedfs", channel="latest/edge")
    juju.wait(
        lambda status: jubilant.all_active(status, "seaweedfs"),
        timeout=15 * 10,
        delay=5,
        successes=3,
    )


@pytest.fixture(scope="module")
@retry(stop=stop_after_attempt(20), wait=wait_fixed(2))
def bucket(juju, seaweedfs):
    endpoint = f"http://{get_unit_ip_address(juju, 'seaweedfs', 0)}:8333"
    requests.put(f"{endpoint}/tempo/")


@given("a juju model")
@when("you run terraform apply using the provided module")
def test_terraform_apply(juju, bucket):
    model_output = juju.cli('show-model', '--format', 'json', include_model=False)
    model_uuid = json.loads(model_output)[juju.model]['model-uuid']
    endpoint = f"http://{get_unit_ip_address(juju, 'seaweedfs', 0)}:8333"
    subprocess.run(shlex.split(f"terraform -chdir={THIS_DIRECTORY} init"), check=True)
    subprocess.run(
        shlex.split(
            f'terraform -chdir={THIS_DIRECTORY} apply -var="channel={CHARM_CHANNEL}" '
            f'-var="model_uuid={model_uuid}" -var "endpoint={endpoint}" -auto-approve'
        ),
        check=True,
    )


@then("tempo charms are deployed and active")
def test_active(juju):
    juju.wait(
        lambda status: jubilant.all_active(
            status,
            "tempo",
            "tempo-compactor",
            "tempo-query-frontend",
            "tempo-distributor",
            "tempo-ingester",
            "tempo-querier",
        ),
        timeout=60 * 30,
    )
