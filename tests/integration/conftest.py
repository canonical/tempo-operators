# Copyright 2025 Canonical Ltd.
# See LICENSE file for licensing details.
import logging
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Literal, Sequence, List

import jubilant
from jubilant import Juju
from minio import Minio
from pytest import fixture
from pytest_jubilant import pack_charm, get_resources

BUCKET_NAME = "tempo"
MINIO_APP = "minio"
SSC_APP = "ssc"
S3_APP = "s3-integrator"
WORKER_APP = "tempo-worker"
TEMPO_APP = "tempo"
TRAEFIK_APP = "trfk"
PROMETHEUS_APP = "prometheus"
LOKI_APP = "loki"

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

logger = logging.getLogger(__name__)


def pytest_addoption(parser):
    parser.addoption(
        "--tls",
        action="store",
        choices=["true", "false"],
        default="false",
        help="Enable TLS (true/false)",
    )
    parser.addoption(
        "--monolithic",
        action="store",
        choices=["true", "false"],
        default="false",
        help="Enable monolithic mode (true/false)",
    )
    parser.addoption(
        "--ingress",
        action="store",
        choices=["true", "false"],
        default="false",
        help="Enable ingress (true/false)",
    )


@fixture(scope="session")
def coordinator_charm():
    """Pyroscope coordinator used for integration testing."""
    return _charm_and_channel_and_resources(
        "coordinator", "COORDINATOR_CHARM_PATH", "COORDINATOR_CHARM_CHANNEL"
    )


@fixture(scope="session")
def worker_charm():
    """Pyroscope worker used for integration testing."""
    return _charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )


@fixture(scope="session")
def tls(pytestconfig):
    """Run with or without tls."""
    return pytestconfig.getoption("tls") == "true"


@fixture(scope="session")
def ingress(pytestconfig):
    """Run with or without ingress."""
    return pytestconfig.getoption("ingress") == "true"


@fixture(scope="session")
def monolithic(pytestconfig):
    """Run in monolithic or fully monolithic mode."""
    return pytestconfig.getoption("monolithic") == "true"


@fixture(scope="session")
def all_tempo_apps(monolithic):
    return (WORKER_APP,) if monolithic else ALL_WORKERS


def _charm_and_channel_and_resources(
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
            pth = pack_charm(Path() / role).charm.absolute()
        except subprocess.CalledProcessError:
            logger.warning(f"Failed to build Tempo {role}. Trying again!")
            continue
        os.environ[charm_path_key] = str(pth)
        return pth, None, get_resources(pth.parent / role)
    raise subprocess.CalledProcessError


def _deploy_monolithic_cluster(juju: Juju, coordinator_deployed_as=None):
    """Deploy a tempo-monolithic cluster."""
    worker_charm_url, channel, resources = _charm_and_channel_and_resources(
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


def _deploy_distributed_cluster(
    juju: Juju, roles: Sequence[str] = tuple(ALL_ROLES), coordinator_deployed_as=None
):
    """Deploy a tempo monolithic cluster."""
    worker_charm_url, channel, resources = _charm_and_channel_and_resources(
        "worker", "WORKER_CHARM_PATH", "WORKER_CHARM_CHANNEL"
    )

    all_workers = []

    for role in roles or ALL_ROLES:
        worker_name = f"{WORKER_APP}-{role.replace('_', '-')}"
        all_workers.append(worker_name)

        juju.deploy(
            worker_charm_url,
            app=worker_name,
            channel=channel,
            trust=True,
            config={"role-all": False, f"role-{role}": True},
            resources=resources,
        )

    _deploy_cluster(juju, all_workers, coordinator_deployed_as=coordinator_deployed_as)


def deploy_s3(juju, bucket_name: str, s3_integrator_app: str):
    logger.info(f"deploying {s3_integrator_app=}")
    juju.deploy(
        "s3-integrator", s3_integrator_app, channel="2/edge", base="ubuntu@24.04"
    )

    logger.info(f"provisioning {bucket_name=} on {s3_integrator_app=}")
    # do not use helpers.get_unit_ip_address to avoid circular deps
    minio_addr = juju.status().apps[MINIO_APP].units[f"{MINIO_APP}/0"].address
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
        error=jubilant.any_error,
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
        coordinator_charm_url, channel, resources = _charm_and_channel_and_resources(
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

    _deploy_and_configure_minio(juju)

    deploy_s3(juju, bucket_name=BUCKET_NAME, s3_integrator_app=S3_APP)
    juju.integrate(coordinator_app + ":s3", S3_APP + ":s3-credentials")

    logger.info("waiting for cluster to be active/idle...")
    juju.wait(
        lambda status: jubilant.all_active(status, coordinator_app, *workers, S3_APP),
        timeout=2000,
        delay=5,
        successes=3,
    )


@contextmanager
def _tls_ctx(active: bool, juju: Juju, all_tempo_apps: List[str]):
    """Context manager to set up tls integration for tempo and s3 integrator."""
    if not active:  # a bit ugly, but nicer than using a nullcontext
        yield
        return

    logger.info("adding TLS")
    juju.deploy("self-signed-certificates", SSC_APP)
    juju.integrate(TEMPO_APP + ":certificates", SSC_APP)

    logger.info("waiting for active...")
    juju.wait(
        lambda status: jubilant.all_active(status, *all_tempo_apps, S3_APP),
        timeout=2000,
        delay=5,
        successes=3,
    )
    logger.info("TLS ready")

    yield

    juju.remove_application(SSC_APP)


@contextmanager
def _ingress_ctx(active: bool, juju: Juju, all_tempo_apps: List[str], tls: bool):
    """Context manager to set up ingress integration for tempo and workers."""
    if not active:  # a bit ugly, but nicer than using a nullcontext
        yield
        return

    logger.info("adding ingress")
    juju.deploy("traefik-k8s", TRAEFIK_APP)
    juju.integrate(TRAEFIK_APP + ":traefik-route", TEMPO_APP)
    if tls:
        juju.integrate(TRAEFIK_APP + ":certificates", SSC_APP)

    logger.info("waiting for active...")
    juju.wait(
        lambda status: jubilant.all_active(status, *all_tempo_apps, TRAEFIK_APP),
        timeout=2000,
        delay=5,
        successes=3,
    )
    logger.info("ingress ready")

    yield

    juju.remove_application(TRAEFIK_APP)


@contextmanager
def _monolithic_ctx(monolithic, juju, setup, teardown, all_tempo_apps):
    if setup:
        if monolithic:
            logger.info("deploying monolithic cluster...")
            _deploy_distributed_cluster(juju)
        else:
            logger.info("deploying monolithic cluster...")
            _deploy_monolithic_cluster(juju)
        logger.info("cluster deployed.")

    yield

    if teardown:
        logger.info("tearing down all apps...")
        for app in all_tempo_apps:
            juju.remove_application(app)


@fixture(scope="module", autouse=True)
def deployment_context(
    tls: bool,
    monolithic: bool,
    ingress: bool,
    coordinator_charm: str,
    worker_charm: str,
    pytestconfig,
    all_tempo_apps: List[str],
    juju: Juju,
):
    """Setup and teardown each test module depending on the parameters that the tests are running with.

    Can be monolithic or distributed, can have tls or no tls, can have ingress or no ingress.
    In any combination.
    """
    with _monolithic_ctx(
        monolithic,
        juju=juju,
        setup=not pytestconfig.getoption("--no-setup"),
        teardown=not pytestconfig.getoption("--no-teardown"),
        all_tempo_apps=all_tempo_apps,
    ):
        with _tls_ctx(tls, juju=juju, all_tempo_apps=all_tempo_apps):
            with _ingress_ctx(
                ingress, juju=juju, all_tempo_apps=all_tempo_apps, tls=tls
            ):
                yield juju
