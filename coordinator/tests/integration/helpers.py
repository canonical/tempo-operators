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
from jubilant import Juju, all_active
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
TRACEGEN_SCRIPT_PATH = Path() / "scripts" / "tracegen.py"

# Application names used uniformly across the tests
MINIO_APP = "minio"
S3_APP = "seaweedfs"
PROMETHEUS_APP = "prometheus"
WORKER_APP = "tempo-worker"
TEMPO_APP = "tempo"
SSC_APP = "ssc"
TRAEFIK_APP = "trfk"
ISTIO_APP = "istio-k8s"
ISTIO_BEACON_APP = "istio-beacon-k8s"

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


def deploy_tempo(juju, name=TEMPO_APP):
    metadata = yaml.safe_load(Path("./charmcraft.yaml").read_text())
    resources = {
        image_name: image_meta["upstream-source"]
        for image_name, image_meta in metadata["resources"].items()
    }

    juju.deploy(_get_tempo_charm(), name, resources=resources, trust=True)


def _deploy_cluster(
    juju: Juju,
    workers: Sequence[str],
    s3=S3_APP,
    coordinator: str = TEMPO_APP,
    wait_for_idle: bool = True,
):
    if coordinator not in juju.status().apps:
        deploy_tempo(juju, name=coordinator)

    for worker in workers:
        juju.integrate(coordinator, worker)
        # if we have an explicit metrics generator worker, we need to integrate with prometheus not to be in blocked
        if "metrics-generator" in worker:
            juju.integrate(
                PROMETHEUS_APP + ":receive-remote-write",
                coordinator + ":send-remote-write",
            )
    juju.deploy("seaweedfs-k8s", s3, channel="edge")
    juju.integrate(coordinator, s3)

    if wait_for_idle:
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
    wait_for_idle: bool = True,
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
        juju,
        [worker],
        coordinator=coordinator,
        s3=s3,
        wait_for_idle=wait_for_idle,
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


def deploy_istio(juju: Juju):
    """Deploy Istio service mesh."""
    juju.deploy(
        "istio-k8s",
        app=ISTIO_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )


def deploy_istio_beacon(juju: Juju):
    """Deploy Istio beacon for ambient mode support."""
    juju.deploy(
        "istio-beacon-k8s",
        app=ISTIO_BEACON_APP,
        channel=INTEGRATION_TESTERS_CHANNEL,
        trust=True,
    )


def deploy_distributed_cluster(
    juju: Juju,
    roles: Sequence[str],
    worker: str = WORKER_APP,
    coordinator: str = TEMPO_APP,
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

    return _deploy_cluster(juju, all_workers, coordinator=coordinator)


def get_traces(
    tempo_host: str,
    service_name="tracegen",
    tls=True,
    nonce: Optional[str] = None,
    source_pod: Optional[str] = None,
    juju: Optional[Juju] = None,
):
    # query params are logfmt-encoded. Space-separated.
    nonce_param = f"%20tracegen.nonce={nonce}" if nonce else ""
    url = f"{'https' if tls else 'http'}://{tempo_host}:3200/api/search?tags=service.name={service_name}{nonce_param}"

    if source_pod and juju:
        # Run curl from specified pod using juju exec
        logger.info(f"Running curl from pod {source_pod} to {url}")
        result = juju.exec(f"curl -s {url}", unit=source_pod)
        response_text = result.stdout
        logger.info(f"Pod response: {response_text}")
        traces = json.loads(response_text)["traces"]
        return traces
    else:
        # Run from host (backward compatibility)
        req = requests.get(
            url,
            verify=False,
            timeout=5,
        )
        assert req.status_code == 200, req.reason
        traces = json.loads(req.text)["traces"]
        return traces


# retry up to 20 times, waiting 20 seconds between attempts
@retry(stop=stop_after_attempt(20), wait=wait_fixed(20))
def get_traces_patiently(
    tempo_host,
    service_name="tracegen",
    tls=True,
    nonce: Optional[str] = None,
    source_pod: Optional[str] = None,
    juju: Optional[Juju] = None,
):
    logger.info(f"polling {tempo_host} for service {service_name!r} traces...")
    traces = get_traces(
        tempo_host,
        service_name=service_name,
        tls=tls,
        nonce=nonce,
        source_pod=source_pod,
        juju=juju,
    )
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
    juju.cli("scp", str(TRACEGEN_SCRIPT_PATH), f"{TEMPO_APP}/0:tracegen.py")
    juju.cli(
        "ssh",
        f"{TEMPO_APP}/0",
        # starting ubuntu@24.04, we can't install system-wide packages using `python -m pip install xyz`
        # use uv to create a venv for tracegen
        f"apt update -y && "
        f"apt install git -y && "
        f"curl -LsSf https://astral.sh/uv/install.sh | sh",
    )

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
    with_deps = " ".join(f"--with '{dep}'" for dep in tracegen_deps)
    cmd = (
        f"juju ssh -m {juju.model} {TEMPO_APP}/0 "
        f"TRACEGEN_SERVICE='{service_name or ''}' "
        f"TRACEGEN_ENDPOINT='{endpoint}' "
        f"TRACEGEN_VERBOSE='{verbose}' "
        f"TRACEGEN_PROTOCOL='{proto}' "
        f"TRACEGEN_CERT='{CA_CERT_PATH if use_cert else ''}' "
        f"TRACEGEN_NONCE='{nonce or ''}' "
        f"~/.local/bin/uv run {with_deps} tracegen.py"
    )

    logger.info(f"running tracegen with {cmd!r}")

    print(cmd)
    lexed_cmd = shlex.split(cmd)
    print(" ".join(lexed_cmd))
    out = subprocess.run(lexed_cmd, text=True, capture_output=True, check=True)
    logger.info(f"tracegen completed; stdout={out.stdout!r}")
    return out


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
    )[TRAEFIK_APP]["url"].split("://")[1]


def service_mesh(
    enable: bool,
    juju: Juju,
    beacon_app_name: str,
    apps_to_be_related_with_beacon: List[str],
):
    """Enable or disable the service-mesh in the model.

    This puts the entire model, that the beacon app is part of, on mesh.
    This integrates the apps_to_be_related_with_beacon with the beacon app via the `service-mesh` relation.
    """
    juju.config(ISTIO_BEACON_APP, {"model-on-mesh": str(enable).lower()})
    # lets wait for all active state before further actions.
    # the wait is necessary to make sure all the charms have recovered from the network changes.
    juju.wait(
        all_active,
        timeout=1000,
    )
    if enable:
        for app in apps_to_be_related_with_beacon:
            juju.integrate(beacon_app_name + ":service-mesh", app + ":service-mesh")
    else:
        for app in apps_to_be_related_with_beacon:
            juju.remove_relation(
                beacon_app_name + ":service-mesh", app + ":service-mesh", force=True
            )
    juju.wait(
        all_active,
        timeout=1000,
        delay=5,
        successes=5,
    )
