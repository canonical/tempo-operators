# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Sequence

import jubilant
import pytest
from jubilant import Juju
from minio import Minio
from pytest import fixture
from pytest_jubilant import pack, get_resources

from tests.integration.helpers import get_unit_ip_address

PROMETHEUS_APP = "prometheus"
BUCKET_NAME = "tempo"
MINIO_APP = "minio"
SSC_APP = "ssc"
S3_APP = "s3-integrator"
WORKER_APP = "tempo-worker"
TEMPO_APP = "tempo"
TRAEFIK_APP = "trfk"
# we don't import this from the coordinator module because that'd mean we need to
# bring in the whole charm's dependencies just to run the integration tests
ALL_ROLES = [
    "querier",
    "query_frontend",
    "ingester",
    "distributor",
    "compactor",
    "metrics_generator",
]
ALL_WORKERS = [f"{WORKER_APP}-" + role.replace("_", "-") for role in ALL_ROLES]

ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
S3_CREDENTIALS = {
    "access-key": ACCESS_KEY,
    "secret-key": SECRET_KEY,
}
INTEGRATION_TESTERS_CHANNEL = "2/edge"

logger = logging.getLogger(__name__)


@fixture(scope="session")
def coordinator_charm():
    """Pyroscope coordinator used for integration testing."""
    return charm_and_channel_and_resources(
        "coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL"
    )


@fixture(scope="session")
def worker_charm():
    """Pyroscope worker used for integration testing."""
    return charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )


def charm_and_channel_and_resources(
    role: Literal["coordinator", "worker"], charm_path_key: str, charm_channel_key: str
):
    """Pyrosocope coordinator or worker charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    """
    # deploy charm from charmhub
    if channel_from_env := os.getenv(charm_channel_key):
        charm = f"tempo-{role}-k8s"
        logger.info(f"Using published {charm} charm from {channel_from_env}")
        return charm, channel_from_env, None
    # else deploy from a charm packed locally
    elif path_from_env := os.getenv(charm_path_key):
        charm_path = Path(path_from_env).absolute()
        logger.info("Using local {role} charm: %s", charm_path)
        return (
            charm_path,
            None,
            get_resources(charm_path.parent),
        )
    # else try to pack the charm
    for _ in range(3):
        logger.info(f"packing Tempo {role} charm...")
        try:
            pth = pack(Path() / role)
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to build Tempo {role}. Trying again!")
            continue
        os.environ[charm_path_key] = str(pth)
        return pth, None, get_resources(Path().parent / role)
    raise subprocess.CalledProcessError


def _deploy_monolithic_cluster(juju: Juju, coordinator_deployed_as=None):
    """Deploy a tempo-monolithic cluster."""
    worker_charm_url, channel, resources = charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )

    juju.deploy(
        worker_charm_url,
        app=WORKER_APP,
        channel=channel,
        trust=True,
        resources=resources,
    )
    _deploy_cluster(juju, [WORKER_APP], coordinator_deployed_as=coordinator_deployed_as)


def deploy_prometheus(juju: Juju):
    """Deploy a pinned revision of prometheus that we know to work."""
    juju.deploy(
        "prometheus-k8s",
        app=PROMETHEUS_APP,
        revision=254,  # what's on 2/edge at July 17, 2025.
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )


def _deploy_distributed_cluster(
    juju: Juju, roles: Sequence[str] = tuple(ALL_ROLES), coordinator_deployed_as=None
):
    """Deploy a tempo distributed cluster."""
    worker_charm_url, channel, resources = charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )

    all_workers = []

    for role in roles or ALL_ROLES:
        role_sanitized = role.replace("_", "-")
        worker_name = f"{WORKER_APP}-{role_sanitized}"
        all_workers.append(worker_name)

        juju.deploy(
            worker_charm_url,
            app=worker_name,
            channel=channel,
            trust=True,
            config={"role-all": False, f"role-{role_sanitized}": True},
            resources=resources,
        )

    deploy_prometheus(juju)

    _deploy_cluster(juju, all_workers, coordinator_deployed_as=coordinator_deployed_as)


def deploy_s3(juju, bucket_name: str, s3_integrator_app: str):
    logger.info(f"deploying {s3_integrator_app=}")

    juju.deploy(
        "s3-integrator",
        s3_integrator_app,
        channel=INTEGRATION_TESTERS_CHANNEL,
        base="ubuntu@24.04",
        # latest revision of s3-integrator creates buckets under relation name, we pin to a working version
        revision=157,
    )

    logger.info(f"provisioning {bucket_name=} on {s3_integrator_app=}")
    minio_addr = get_unit_ip_address(juju, MINIO_APP, 0)
    mc_client = Minio(
        f"{minio_addr}:9000",
        **{key.replace("-", "_"): value for key, value in S3_CREDENTIALS.items()},
        secure=False,
    )
    # create tempo bucket
    found = mc_client.bucket_exists(bucket_name)
    if not found:
        mc_client.make_bucket(bucket_name)

    logger.info("configuring s3 integrator...")
    secret_uri = juju.cli(
        "add-secret",
        f"{s3_integrator_app}-creds",
        *(f"{key}={val}" for key, val in S3_CREDENTIALS.items()),
    )
    juju.cli("grant-secret", f"{s3_integrator_app}-creds", s3_integrator_app)

    # configure s3-integrator
    juju.config(
        s3_integrator_app,
        {
            "endpoint": f"minio-0.minio-endpoints.{juju.model}.svc.cluster.local:9000",
            "bucket": bucket_name,
            "credentials": secret_uri.strip(),
        },
    )


def _deploy_and_configure_minio(juju: Juju):
    juju.deploy(MINIO_APP, channel="edge", trust=True, config=S3_CREDENTIALS)
    juju.wait(
        lambda status: status.apps[MINIO_APP].is_active,
        delay=5,
        successes=3,
        timeout=2000,
    )


def _deploy_cluster(
    juju: Juju, workers: Sequence[str], coordinator_deployed_as: str = None
):
    logger.info("deploying cluster")

    if coordinator_deployed_as:
        coordinator_app = coordinator_deployed_as
    else:
        coordinator_charm_url, channel, resources = charm_and_channel_and_resources(
            "coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL"
        )
        juju.deploy(
            coordinator_charm_url,
            TEMPO_APP,
            channel=channel,
            resources=resources,
            trust=True,
        )
        coordinator_app = TEMPO_APP

    for worker in workers:
        juju.integrate(coordinator_app, worker)
        # if we have an explicit metrics generator worker, we need to integrate with prometheus not to be in blocked
        if "metrics-generator" in worker:
            juju.integrate(
                PROMETHEUS_APP + ":receive-remote-write",
                coordinator_app + ":send-remote-write",
            )

    _deploy_and_configure_minio(juju)

    deploy_s3(juju, bucket_name=BUCKET_NAME, s3_integrator_app=S3_APP)
    juju.integrate(coordinator_app + ":s3", S3_APP + ":s3-credentials")

    logger.info("waiting for cluster to be active/idle...")
    juju.wait(
        lambda status: jubilant.all_active(status, coordinator_app, *workers, S3_APP),
        timeout=3000,
        delay=5,
        successes=3,
    )


@contextmanager
def _tls_ctx(active: bool, juju: Juju, distributed: bool):
    """Context manager to set up tls integration for tempo and s3 integrator."""
    if not active:  # a bit ugly, but nicer than using a nullcontext
        yield
        return

    logger.info("adding TLS")
    juju.deploy("self-signed-certificates", SSC_APP)
    juju.integrate(SSC_APP + ":certificates", TEMPO_APP + ":certificates")

    logger.info("waiting for active...")
    juju.wait(
        lambda status: jubilant.all_active(
            status, TEMPO_APP, *(ALL_WORKERS if distributed else (WORKER_APP,)), S3_APP
        ),
        timeout=2000,
        delay=5,
        successes=3,
    )
    logger.info("TLS ready")

    yield

    juju.remove_application(SSC_APP)


@pytest.fixture
def do_setup(pytestconfig):
    return not pytestconfig.getoption("--no-setup")


@pytest.fixture
def do_teardown(pytestconfig):
    return not pytestconfig.getoption("--no-teardown")


@contextmanager
def deployment_factory(tls, distributed, juju, do_setup, do_teardown):
    if do_setup:
        if distributed:
            logger.info("deploying distributed cluster...")
            _deploy_distributed_cluster(juju)
        else:
            logger.info("deploying monolithic cluster...")
            _deploy_monolithic_cluster(juju)
        logger.info("cluster deployed.")

    with _tls_ctx(tls, juju=juju, distributed=distributed):
        yield juju

    if do_teardown:
        logger.info("tearing down all apps...")
        for app_to_remove in {
            TEMPO_APP,
            *(ALL_WORKERS if distributed else (WORKER_APP,)),
        }:
            juju.remove_application(app_to_remove)
