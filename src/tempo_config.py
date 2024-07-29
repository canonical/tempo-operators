# Copyright 2023 Canonical
# See LICENSE file for licensing details.

"""Helper module for interacting with the Tempo configuration."""
import enum
import logging
import re
from enum import Enum, unique
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


# TODO: inherit enum.StrEnum when jammy is no longer supported.
# https://docs.python.org/3/library/enum.html#enum.StrEnum
@unique
class TempoRole(str, Enum):
    """Tempo component role names.

    References:
     arch:
      -> https://grafana.com/docs/tempo/latest/operations/architecture/
     config:
      -> https://grafana.com/docs/tempo/latest/configuration/#server
    """

    # scalable-single-binary is a bit too long to type
    all = "all"  # default, meta-role. gets remapped to scalable-single-binary by the worker.

    querier = "querier"
    query_frontend = "query-frontend"
    ingester = "ingester"
    distributor = "distributor"
    compactor = "compactor"
    metrics_generator = "metrics-generator"

    @staticmethod
    def all_nonmeta():
        return {
            TempoRole.querier,
            TempoRole.query_frontend,
            TempoRole.ingester,
            TempoRole.distributor,
            TempoRole.compactor,
        }


META_ROLES = {
    "all": set(TempoRole.all_nonmeta()),
}
"""Tempo component meta-role names."""

MINIMAL_DEPLOYMENT = {
    TempoRole.querier: 1,
    TempoRole.query_frontend: 1,
    TempoRole.ingester: 1,
    TempoRole.distributor: 1,
    TempoRole.compactor: 1,
}
"""The minimal set of roles that need to be allocated for the
deployment to be considered consistent (otherwise we set blocked)."""

RECOMMENDED_DEPLOYMENT = {
    TempoRole.querier.value: 1,
    TempoRole.query_frontend.value: 1,
    TempoRole.ingester.value: 3,
    TempoRole.distributor.value: 1,
    TempoRole.compactor.value: 1,
    TempoRole.metrics_generator.value: 1,
}

"""
The set of roles that need to be allocated for the
deployment to be considered robust according to Grafana Tempo's
Helm chart configurations.
https://github.com/grafana/helm-charts/blob/main/charts/tempo-distributed/
"""


class TempoRolesConfig:
    """Define the configuration for Tempo roles."""

    roles: Iterable[str] = {role for role in TempoRole}
    meta_roles: Mapping[str, Iterable[str]] = META_ROLES
    minimal_deployment: Iterable[str] = MINIMAL_DEPLOYMENT
    recommended_deployment: Dict[str, int] = RECOMMENDED_DEPLOYMENT


class ClientAuthTypeEnum(str, enum.Enum):
    """Client auth types."""

    # Possible values https://pkg.go.dev/crypto/tls#ClientAuthType
    VERIFY_CLIENT_CERT_IF_GIVEN = "VerifyClientCertIfGiven"
    NO_CLIENT_CERT = "NoClientCert"
    REQUEST_CLIENT_CERT = "RequestClientCert"
    REQUIRE_ANY_CLIENT_CERT = "RequireAnyClientCert"
    REQUIRE_AND_VERIFY_CLIENT_CERT = "RequireAndVerifyClientCert"


class InvalidConfigurationError(Exception):
    """Invalid configuration."""

    pass


class Kvstore(BaseModel):
    """Kvstore schema."""

    store: str = "memberlist"


class Ring(BaseModel):
    """Ring schema."""

    kvstore: Kvstore
    replication_factor: int


class Memberlist(BaseModel):
    """Memberlist schema."""

    abort_if_cluster_join_fails: bool
    bind_port: int
    join_members: List[str]
    tls_enabled: bool = False
    tls_cert_path: Optional[str] = None
    tls_key_path: Optional[str] = None
    tls_ca_path: Optional[str] = None
    tls_server_name: Optional[str] = None


class ClientTLS(BaseModel):
    """Client tls config schema."""

    tls_enabled: bool
    tls_cert_path: str
    tls_key_path: str
    tls_ca_path: str
    tls_server_name: str


class Client(BaseModel):
    """Client schema."""

    grpc_client_config: ClientTLS


class Distributor(BaseModel):
    """Distributor schema."""

    ring: Optional[Ring] = None
    receivers: Dict[str, Any]


class Ingester(BaseModel):
    """Ingester schema."""

    trace_idle_period: str
    max_block_bytes: int
    max_block_duration: str
    lifecycler: Optional[Ring] = None


class FrontendWorker(BaseModel):
    """FrontendWorker schema."""

    frontend_address: str
    grpc_client_config: Optional[ClientTLS] = None


class Querier(BaseModel):
    """Querier schema."""

    frontend_worker: FrontendWorker


class TLS(BaseModel):
    """TLS configuration schema."""

    model_config = ConfigDict(
        # Allow serializing enum values.
        use_enum_values=True
    )
    """Pydantic config."""

    cert_file: str
    key_file: str
    client_ca_file: str
    client_auth_type: ClientAuthTypeEnum = ClientAuthTypeEnum.VERIFY_CLIENT_CERT_IF_GIVEN


class Server(BaseModel):
    """Server schema."""

    http_listen_port: int
    grpc_listen_port: int
    http_tls_config: Optional[TLS] = None
    grpc_tls_config: Optional[TLS] = None


class Compaction(BaseModel):
    """Compaction schema."""

    compaction_window: str
    max_compaction_objects: int
    block_retention: str
    compacted_block_retention: str


class Compactor(BaseModel):
    """Compactor schema."""

    ring: Optional[Ring] = None
    compaction: Compaction


class Pool(BaseModel):
    """Pool schema."""

    max_workers: int
    queue_depth: int


class Wal(BaseModel):
    """Wal schema."""

    path: Path


class S3(BaseModel):
    """S3 config schema."""

    model_config = ConfigDict(populate_by_name=True)
    """Pydantic config."""

    # Use aliases to override keys in `coordinator::_s3_config`
    # to align with upstream Tempo's configuration keys: `bucket`, `access_key`, `secret_key`.
    bucket_name: str = Field(alias="bucket")
    access_key_id: str = Field(alias="access_key")
    endpoint: str
    secret_access_key: str = Field(alias="secret_key")
    insecure: bool = False

    @model_validator(mode="before")  # pyright: ignore
    @classmethod
    def set_insecure(cls, data: Any) -> Any:
        if isinstance(data, dict) and data.get("endpoint", None):
            data["insecure"] = False if data["endpoint"].startswith("https://") else True
        return data

    @field_validator("endpoint")
    def remove_scheme(cls, v: str) -> str:
        """Remove the scheme from the s3 endpoint."""
        # remove scheme to avoid "Endpoint url cannot have fully qualified paths." on Tempo startup
        return re.sub(
            rf"^{urlparse(v).scheme}://",
            "",
            v,
        )


class Block(BaseModel):
    """Block schema."""

    version: Optional[str]


class TraceStorage(BaseModel):
    """Trace Storage schema."""

    wal: Wal
    pool: Pool
    backend: str
    s3: S3
    block: Block


class Storage(BaseModel):
    """Storage schema."""

    trace: TraceStorage


class TempoConfig(BaseModel):
    """Tempo config schema."""

    auth_enabled: bool
    server: Server
    distributor: Distributor
    ingester: Ingester
    memberlist: Memberlist
    compactor: Compactor
    querier: Querier
    storage: Storage
    ingester_client: Optional[Client] = None
    metrics_generator_client: Optional[Client] = None
