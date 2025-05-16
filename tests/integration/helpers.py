import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional, Sequence, Union

import jubilant
import requests
import yaml
from cosl.coordinated_workers.nginx import CA_CERT_PATH
from jubilant import Juju
from minio import Minio
from pytest_jubilant import pack_charm
from tenacity import retry, stop_after_attempt, wait_fixed

from tempo_config import TempoRole

_JUJU_DATA_CACHE = {}
_JUJU_KEYS = ("egress-subnets", "ingress-address", "private-address")
ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
BUCKET_NAME = "tempo"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())

TEMPO_RESOURCES = {
    image_name: image_meta["upstream-source"] for image_name, image_meta in METADATA["resources"].items()
}

# Application names used uniformly across the tests
MINIO_APP = "minio"
S3_APP = "s3-integrator"
PROMETHEUS_APP = "prometheus"
WORKER_APP = "tempo-worker"
TEMPO_APP = "tempo"
SSC_APP = "ssc"
TRAEFIK_APP = "trfk"

ALL_ROLES = [role.value for role in TempoRole.all_nonmeta()]
ALL_WORKERS = [f"{WORKER_APP}-" + role for role in ALL_ROLES]

protocols_endpoints = {
    "jaeger_thrift_http": "{scheme}://{hostname}:14268/api/traces?format=jaeger.thrift",
    "zipkin": "{scheme}://{hostname}:9411/v1/traces",
    "jaeger_grpc": "{hostname}:14250",
    "otlp_http": "{scheme}://{hostname}:4318/v1/traces",
    "otlp_grpc": "{hostname}:4317",
}

api_endpoints = {
    "tempo_http": "{scheme}://{hostname}:3200/api",
    "tempo_grpc": "{hostname}:9096",
}

logger = logging.getLogger(__name__)


def run_command(model_name: str, app_name: str, unit_num: int, command: list) -> bytes:
    cmd = ["juju", "ssh", "--model", model_name, f"{app_name}/{unit_num}", *command]
    try:
        res = subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        logger.info(res)
    except subprocess.CalledProcessError as e:
        logger.error(e.stdout.decode())
        raise e
    return res.stdout


def get_app_ip_address(juju: Juju, app_name):
    """Return a juju application's IP address."""
    return juju.status().apps[app_name].address


def get_unit_ip_address(juju: Juju, app_name: str, unit_no: int):
    """Return a juju unit's IP address."""
    return juju.status().apps[app_name].units[f"{app_name}/{unit_no}"].address


def _deploy_and_configure_minio(juju: Juju):
    keys = {
        "access-key": ACCESS_KEY,
        "secret-key": SECRET_KEY,
    }
    juju.deploy(MINIO_APP, channel="edge", trust=True, config=keys)
    juju.wait(
        lambda status: status.apps[MINIO_APP].is_active,
        error=jubilant.any_error,
    )
    minio_addr = get_unit_ip_address(juju, MINIO_APP, 0)

    mc_client = Minio(
        f"{minio_addr}:9000",
        access_key=ACCESS_KEY,
        secret_key=SECRET_KEY,
        secure=False,
    )

    # create tempo bucket
    found = mc_client.bucket_exists(BUCKET_NAME)
    if not found:
        mc_client.make_bucket(BUCKET_NAME)

    # configure s3-integrator
    juju.config(S3_APP, {
        "endpoint": f"minio-0.minio-endpoints.{juju.model}.svc.cluster.local:9000",
        "bucket": BUCKET_NAME,
    })
    task = juju.run(S3_APP + "/0", "sync-s3-credentials", params=keys)
    assert task.status == "completed"


def tempo_worker_charm_and_channel_and_resources():
    """Tempo worker charm used for integration testing.

    Build once per session and reuse it in all integration tests to save some minutes/hours.
    You can also set `WORKER_CHARM_PATH` env variable to use an already existing built charm.
    """
    if path_from_env := os.getenv("WORKER_CHARM_PATH"):
        worker_charm_path = Path(path_from_env).absolute()
        logger.info("Using local tempo worker charm: %s", worker_charm_path)
        return (
            worker_charm_path,
            None,
            get_resources(worker_charm_path.parent),
        )
    return "tempo-worker-k8s", "edge", None


def get_resources(path: Union[str, Path]):
    meta = yaml.safe_load((Path(path) / "charmcraft.yaml").read_text())
    resources_meta = meta.get("resources", {})
    return {res_name: res_meta["upstream-source"] for res_name, res_meta in resources_meta.items()}


def _get_tempo_charm():
    if tempo_charm := os.getenv("CHARM_PATH"):
        return tempo_charm

    # Intermittent issue where charmcraft fails to build the charm for an unknown reason.
    # Retry building the charm
    for _ in range(3):
        logger.info("packing...")
        try:
            pth = pack_charm().charm.absolute()
        except subprocess.CalledProcessError:
            logger.warning("Failed to build Tempo coordinator. Trying again!")
            continue
        os.environ["CHARM_PATH"] = str(pth)
        return pth
    raise err  # noqa

def _deploy_cluster(juju: Juju, workers: Sequence[str], tempo_deployed_as: str = None):
    if tempo_deployed_as:
        tempo_app = tempo_deployed_as
    else:
        juju.deploy(
            _get_tempo_charm(), TEMPO_APP, resources=TEMPO_RESOURCES, trust=True
        )
        tempo_app = TEMPO_APP

    juju.deploy(S3_APP, channel="edge")

    juju.integrate(tempo_app + ":s3", S3_APP + ":s3-credentials")
    for worker in workers:
        juju.integrate(tempo_app + ":tempo-cluster", worker + ":tempo-cluster")
        # if we have an explicit metrics generator worker, we need to integrate with prometheus not to be in blocked
        if "metrics-generator" in worker:
            juju.integrate(PROMETHEUS_APP + ":receive-remote-write",
                           tempo_app + ":send-remote-write")

    _deploy_and_configure_minio(juju)

    juju.wait(
        lambda status: jubilant.all_active(status, tempo_app, *workers, S3_APP),
        timeout=2000,
    )


def deploy_monolithic_cluster(juju: Juju, tempo_deployed_as=None):
    """Deploy a tempo-monolithic cluster.

    `param:tempo_app`: tempo-coordinator is already deployed as this app.
    """
    tempo_worker_charm_url, channel, resources = tempo_worker_charm_and_channel_and_resources()
    juju.deploy(
        tempo_worker_charm_url,
        app=WORKER_APP,
        channel=channel,
        trust=True,
        resources=resources,
    )
    _deploy_cluster(juju, [WORKER_APP], tempo_deployed_as=tempo_deployed_as)


def deploy_distributed_cluster(juju: Juju, roles: Sequence[str], tempo_deployed_as=None):
    """This assumes tempo-coordinator is already deployed as `param:tempo_app`."""
    tempo_worker_charm_url, channel, resources = tempo_worker_charm_and_channel_and_resources()

    all_workers = []

    for role in roles:
        worker_name = f"{WORKER_APP}-{role}"
        all_workers.append(worker_name)

        juju.deploy(
            tempo_worker_charm_url,
            app=worker_name,
            channel=channel,
            trust=True,
            config={"role-all": False, f"role-{role}": True},
            resources=resources,
        )

        if role == "metrics-generator":
            juju.deploy(
                "prometheus-k8s",
                app=PROMETHEUS_APP,
                revision=244, # what's on edge at april 23, 2025.
                channel="latest/edge", # we need the channel for the updates
                trust=True
            )

    _deploy_cluster(juju, all_workers, tempo_deployed_as=tempo_deployed_as)


def get_traces(tempo_host: str, service_name="tracegen", tls=True, nonce:Optional[str]=None):
    # query params are logfmt-encoded. Space-separated.
    nonce_param = f"%20tracegen.nonce={nonce}" if nonce else ""
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search?tags=service.name={service_name}{nonce_param}"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200, req.reason
    traces = json.loads(req.text)["traces"]
    return traces


# retry up to 10 times, waiting 4 seconds between attempts
@retry(stop=stop_after_attempt(10), wait=wait_fixed(4))
def get_traces_patiently(tempo_host, service_name="tracegen", tls=True, nonce:Optional[str] = None):
    logger.info(f"polling {tempo_host} for service {service_name!r} traces...")
    traces = get_traces(tempo_host, service_name=service_name, tls=tls, nonce=nonce)
    assert len(traces) > 0, "no traces found"
    return traces


def emit_trace(
        endpoint,
        juju: Juju,
        nonce: str = None,
        proto: str = "otlp_http",
        service_name: Optional[str] = "tracegen",
        verbose=0,
        use_cert=False,
):
    """Use juju ssh to run tracegen from the tempo charm; to avoid any DNS issues."""
    # SCP tracegen script onto unit and install dependencies
    logger.info(f"pushing tracegen onto {TEMPO_APP}/0")
    tracegen_deps = (
        "protobuf==3.20.*",
        "opentelemetry-exporter-otlp-proto-http==1.21.0",
        "opentelemetry-exporter-otlp-proto-grpc",
        "opentelemetry-exporter-zipkin",
        "opentelemetry-exporter-jaeger",
        # opentelemetry-exporter-jaeger complains about old generated protos
        "protobuf==3.20.*",
        # FIXME: thrift exporter uses thrift package that doesn't work with Python>=3.12
        # There's a fix, but they haven't released a new version of thrift yet.
        "thrift@git+https://github.com/apache/thrift.git@6e380306ef48af4050a61f2f91b3c8380d8e78fb#subdirectory=lib/py",
    )

    juju.cli("scp", str(TRACEGEN_SCRIPT_PATH), f"{TEMPO_APP}/0:tracegen.py")
    juju.cli(
        "ssh",
        f"{TEMPO_APP}/0",
        # starting ubuntu@24.04, we can't install system-wide packages using `python -m pip install xyz`
        # use uv to create a venv for tracegen 
        f"apt update -y && apt install git -y && curl -LsSf https://astral.sh/uv/install.sh | sh && $HOME/.local/bin/uv venv && $HOME/.local/bin/uv pip install {' '.join(tracegen_deps)}",
    )

    cmd = (
        f"juju ssh -m {juju.model} {TEMPO_APP}/0 "
        f"TRACEGEN_SERVICE={service_name or ''} "
        f"TRACEGEN_ENDPOINT={endpoint} "
        f"TRACEGEN_VERBOSE={verbose} "
        f"TRACEGEN_PROTOCOL={proto} "
        f"TRACEGEN_CERT={CA_CERT_PATH if use_cert else ''} "
        f"TRACEGEN_NONCE={nonce or ''} "
        "$HOME/.local/bin/uv run tracegen.py"
    )

    logger.info(f"running tracegen with {cmd!r}")

    out = subprocess.run(shlex.split(cmd), text=True, capture_output=True, check=True)
    logger.info(f"tracegen completed; stdout={out.stdout!r}")


def _get_endpoint(protocol: str, hostname: str, tls: bool):
    protocol_endpoint = protocols_endpoints.get(protocol) or api_endpoints.get(protocol)
    if protocol_endpoint is None:
        assert False, f"Invalid {protocol}"

    if "grpc" in protocol:
        # no scheme in _grpc endpoints
        return protocol_endpoint.format(hostname=hostname)
    else:
        return protocol_endpoint.format(hostname=hostname, scheme="https" if tls else "http")


def get_tempo_ingressed_endpoint(hostname, protocol: str, tls: bool):
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_internal_endpoint(juju: Juju, protocol: str, tls: bool):
    hostname = f"{TEMPO_APP}-0.{TEMPO_APP}-endpoints.{juju.model}.svc.cluster.local"
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_application_endpoint(tempo_ip: str, protocol: str, tls: bool):
    return _get_endpoint(protocol, tempo_ip, tls)

def get_ingress_proxied_hostname(juju: Juju):
    status = juju.status()
    status_msg = status.apps[TRAEFIK_APP].app_status.message

    # hacky way to get ingress hostname, but it's the safest one.
    if "Serving at" not in status_msg:
        raise RuntimeError(
            f"Ingressed hostname is not present in {TRAEFIK_APP} status message."
        )
    return status_msg.replace("Serving at", "").strip()