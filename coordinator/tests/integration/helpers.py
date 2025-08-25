import json
import logging
import os
import shlex
import subprocess
from pathlib import Path
from typing import Optional, Sequence, Union, List, cast, Set

import jubilant
import requests
import yaml
from coordinated_workers.nginx import CA_CERT_PATH
from jubilant import Juju
from minio import Minio
from pytest_jubilant import pack
from tenacity import retry, stop_after_attempt, wait_fixed

from tempo_config import TempoRole

ACCESS_KEY = "accesskey"
SECRET_KEY = "secretkey"
S3_CREDENTIALS = {
    "access-key": ACCESS_KEY,
    "secret-key": SECRET_KEY,
}
BUCKET_NAME = "tempo"
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"
METADATA = yaml.safe_load(Path("./charmcraft.yaml").read_text())

TEMPO_RESOURCES = {
    image_name: image_meta["upstream-source"]
    for image_name, image_meta in METADATA["resources"].items()
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
INTEGRATION_TESTERS_CHANNEL = "2/edge"

logger = logging.getLogger(__name__)


def run_command(model_name: str, app_name: str, unit_num: int, command: list) -> bytes:
    cmd = ["juju", "ssh", "--model", model_name, f"{app_name}/{unit_num}", *command]
    try:
        res = subprocess.run(
            cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT
        )
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


def deploy_s3(juju, bucket_name: str, s3_integrator_app: str):
    """Deploy and configure a s3 integrator.

    Assumes there's a MINIO_APP deployed and ready.
    """
    logger.info(f"deploying {s3_integrator_app=}")
    # latest revision of s3-integrator creates buckets under relation name, we pin to a working version
    juju.deploy(
        "s3-integrator",
        s3_integrator_app,
        channel="2/edge",
        revision=157,
        base="ubuntu@24.04",
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
    if MINIO_APP not in juju.status().apps:
        juju.deploy(MINIO_APP, channel="edge", trust=True, config=S3_CREDENTIALS)

    juju.wait(
        lambda status: status.apps[MINIO_APP].is_active,
        delay=5,
        successes=3,
        timeout=2000,
    )


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
    return "tempo-worker-k8s", INTEGRATION_TESTERS_CHANNEL, None


def get_resources(path: Union[str, Path]):
    meta = yaml.safe_load((Path(path) / "charmcraft.yaml").read_text())
    resources_meta = meta.get("resources", {})
    return {
        res_name: res_meta["upstream-source"]
        for res_name, res_meta in resources_meta.items()
    }


def _get_tempo_charm():
    if tempo_charm := os.getenv("CHARM_PATH"):
        return tempo_charm

    # Intermittent issue where charmcraft fails to build the charm for an unknown reason.
    # Retry building the charm
    for _ in range(3):
        logger.info("packing...")
        try:
            pth = pack()
        except subprocess.CalledProcessError:
            logger.warning("Failed to build Tempo coordinator. Trying again!")
            continue
        os.environ["CHARM_PATH"] = str(pth)
        return pth
    raise err  # noqa


def _deploy_cluster(
    juju: Juju,
    workers: Sequence[str],
    s3=S3_APP,
    coordinator: str = TEMPO_APP,
    bucket_name: str = BUCKET_NAME,
):
    if coordinator not in juju.status().apps:
        juju.deploy(
            _get_tempo_charm(), coordinator, resources=TEMPO_RESOURCES, trust=True
        )

    for worker in workers:
        juju.integrate(coordinator, worker)
        # if we have an explicit metrics generator worker, we need to integrate with prometheus not to be in blocked
        if "metrics-generator" in worker:
            juju.integrate(
                PROMETHEUS_APP + ":receive-remote-write",
                coordinator + ":send-remote-write",
            )

    _deploy_and_configure_minio(juju)

    deploy_s3(juju, bucket_name=bucket_name, s3_integrator_app=s3)
    juju.integrate(coordinator + ":s3", s3 + ":s3-credentials")

    juju.wait(
        lambda status: jubilant.all_active(status, coordinator, *workers, s3),
        timeout=2000,
        delay=5,
        successes=3,
    )


def deploy_monolithic_cluster(
    juju: Juju,
    worker: str = WORKER_APP,
    s3: str = S3_APP,
    coordinator: str = TEMPO_APP,
    bucket_name: str = BUCKET_NAME,
):
    """Deploy a tempo monolithic cluster."""
    tempo_worker_charm_url, channel, resources = (
        tempo_worker_charm_and_channel_and_resources()
    )
    juju.deploy(
        tempo_worker_charm_url,
        app=worker,
        channel=channel,
        trust=True,
        resources=resources,
    )
    _deploy_cluster(
        juju, [worker], coordinator=coordinator, s3=s3, bucket_name=bucket_name
    )


def deploy_prometheus(juju: Juju):
    """Deploy a pinned revision of prometheus that we know to work."""
    juju.deploy(
        "prometheus-k8s",
        app=PROMETHEUS_APP,
        revision=254,  # what's on 2/edge at July 17, 2025.
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )


def deploy_distributed_cluster(
    juju: Juju,
    roles: Sequence[str],
    worker: str = WORKER_APP,
    coordinator: str = TEMPO_APP,
    bucket_name: str = BUCKET_NAME,
):
    """Deploy a tempo distributed cluster."""
    tempo_worker_charm_url, channel, resources = (
        tempo_worker_charm_and_channel_and_resources()
    )

    all_workers = []

    for role in roles:
        worker_name = f"{worker}-{role}"
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
            deploy_prometheus(juju)

    return _deploy_cluster(
        juju, all_workers, coordinator=coordinator, bucket_name=bucket_name
    )


def get_traces(
    tempo_host: str, service_name="tracegen", tls=True, nonce: Optional[str] = None
):
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


# retry up to 20 times, waiting 20 seconds between attempts
@retry(stop=stop_after_attempt(20), wait=wait_fixed(20))
def get_traces_patiently(
    tempo_host, service_name="tracegen", tls=True, nonce: Optional[str] = None
):
    logger.info(f"polling {tempo_host} for service {service_name!r} traces...")
    traces = get_traces(tempo_host, service_name=service_name, tls=tls, nonce=nonce)
    assert len(traces) > 0, "no traces found"
    return traces


def get_ingested_traces_service_names(tempo_host, tls: bool) -> Set[str]:
    """Fetch all ingested traces tags."""
    logger.info(f"querying {tempo_host} for tags...")

    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search/tag/service.name/values"
    req = requests.get(
        url,
        verify=False,
    )
    assert req.status_code == 200, req.reason
    tags = cast(List[str], json.loads(req.text)["tagValues"])
    return set(tags)


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
        return protocol_endpoint.format(
            hostname=hostname, scheme="https" if tls else "http"
        )


def get_tempo_ingressed_endpoint(hostname, protocol: str, tls: bool):
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_internal_endpoint(juju: Juju, protocol: str, tls: bool, unit: int = 0):
    hostname = (
        f"{TEMPO_APP}-{unit}.{TEMPO_APP}-endpoints.{juju.model}.svc.cluster.local"
    )
    return _get_endpoint(protocol, hostname, tls)


def get_tempo_application_endpoint(tempo_ip: str, protocol: str, tls: bool):
    return _get_endpoint(protocol, tempo_ip, tls)


def get_ingress_proxied_hostname(juju: Juju):
    return json.loads(
        juju.run(TRAEFIK_APP + "/0", "show-proxied-endpoints").results[
            "proxied-endpoints"
        ]
    )[TRAEFIK_APP]["url"].split("http://")[1]
