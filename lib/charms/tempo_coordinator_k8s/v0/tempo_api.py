"""tempo_api.

This library implements endpoint wrappers for the tempo-api interface.  The tempo-api interface is used to
transfer information about an instance of Tempo, such as how to access and uniquely identify it.  Typically, this is
useful for charms that operate a Tempo instance to give other applications access to
[Tempo's HTTP API](https://grafana.com/docs/tempo/latest/api_docs/).

## Usage

### Requirer

TempoApiRequirer is a wrapper for pulling data from the tempo-api interface.  To use it in your charm:

* observe the relation-changed event for this relation wherever your charm needs to use this data (this endpoint wrapper
  DOES NOT automatically observe any events)
* wherever you need access to the data, call `TempoApiRequirer(...).get_data()`

An example implementation is:

```python
class FooCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)

        tempo_api = TempoApiRequirer(self.model.relations, "tempo-api")

        self.framework.observe(
            self.on["tempo-api"].relation_changed, self._on_tempo_api_changed
        )

    def do_something_with_metadata(self):
        data = tempo_api.get_data()
        ...
```

Where you also add relation to your `charmcraft.yaml` or `metadata.yaml` (note that TempoApiRequirer is designed for
relating to a single application and must be used with limit=1 as shown below):

```yaml
requires:
  tempo-api:
    limit: 1
    interface: tempo_api
```

### Provider

TempoApiProvider is a wrapper for publishing data to charms related using the tempo-api interface.  Note that
`TempoApiProvider` *does not* manage any events, but instead provides a `publish` method for sending data to
all related applications.  Triggering `publish` appropriately is left to the charm author, although generally you want
to do this at least during the `relation_joined` and `leader_elected` events.  An example implementation is:

```python
class FooCharm(CharmBase):
    def __init__(self, framework):
        super().__init__(framework)
        self.tempo_api = TempoApiProvider(
            relations=self.model.relations,
            relation_name="tempo-api",
            app=self.app,
        )

        self.framework.observe(self.on.leader_elected, self.do_something_to_publish)
        self.framework.observe(
            self._charm.on["tempo-api"].relation_joined, self.do_something_to_publish
        )
        self.framework.observe(
            self.on.some_event_that_changes_tempos_url, self.do_something_to_publish
        )

    def do_something_to_publish(self, e):
        self.tempo_api.publish(...)
```

Where you also add the following to your `charmcraft.yaml` or `metadata.yaml`:

```yaml
provides:
  tempo-api:
    interface: tempo_api
```
"""

import json
import logging
from pathlib import Path
from typing import List, Optional, Union

import yaml
from ops import Application, Relation, RelationMapping
from pydantic import AfterValidator, AnyHttpUrl, BaseModel, Field
from typing_extensions import Annotated

# The unique Charmhub library identifier, never change it
LIBID = "6d55454c9a104113b2bd01e738dd5f99"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 3

PYDEPS = ["pydantic>=2"]

log = logging.getLogger(__name__)

DEFAULT_RELATION_NAME = "tempo-api"

# Define a custom type that accepts AnyHttpUrl and string, but converts to AnyHttpUrl and raises an exception if the
# string is not a valid URL
AnyHttpUrlOrStrUrl = Annotated[Union[AnyHttpUrl, str], AfterValidator(lambda v: AnyHttpUrl(v))]


class TempoApiUrls(BaseModel):
    """Data model for urls Tempo offers for query access, for a given protocol."""

    direct_url: AnyHttpUrlOrStrUrl = Field(
        description="The cluster-internal URL at which this application can be reached for a connection."
        " Typically, this is a Kubernetes FQDN like name.namespace.svc.cluster.local for"
        " connecting to the tempo api from inside the cluster, with scheme and maybe port."
    )
    ingress_url: Optional[AnyHttpUrlOrStrUrl] = Field(
        default=None,
        description="The non-internal URL at which this application can be reached for a connection."
        " Typically, this is an ingress URL.",
    )


class TempoApiAppData(BaseModel):
    """Data model for the tempo-api interface."""

    http: TempoApiUrls = Field(
        description="The URLs at which this application can be reached for an http connection to query Tempo."
    )
    grpc: TempoApiUrls = Field(
        description="The URLs at which this application can be reached for an http connection to query Tempo."
    )


class TempoApiRequirer:
    """Endpoint wrapper for the requirer side of the tempo-api relation."""

    def __init__(
        self,
        relation_mapping: RelationMapping,
        relation_name: str = DEFAULT_RELATION_NAME,
    ) -> None:
        """Initialize the TempoApiRequirer object.

        This object is for accessing data from relations that use the tempo-api interface.  It **does not**
        autonomously handle the events associated with that relation.  It is up to the charm using this object to
        observe those events as they see fit.  Typically, that charm should observe this relation's relation-changed
        event.

        This object requires the relation has limit=1 set in charmcraft.yaml, otherwise it will raise a ValueError
        exception.

        Args:
            relation_mapping: The RelationMapping of a charm (typically `self.model.relations` from within a charm
                              object).
            relation_name: The name of the wrapped relation.
        """
        self._charm_relation_mapping = relation_mapping
        self._relation_name = relation_name

        self._validate_relation_metadata()

    @property
    def relations(self) -> List[Relation]:
        """Return the relation instances for applications related to us on the monitored relation."""
        return self._charm_relation_mapping.get(self._relation_name, [])

    def get_data(self) -> Optional[TempoApiAppData]:
        """Return data from the relation.

        Returns None if no data is available, either because no applications are related to us, or because the related
        application has not sent data.  To distinguish between these cases, check if
        len(tempo_api_requirer.relations)==0.
        """
        relations = self.relations
        if len(relations) == 0:
            return None

        # Being a little cautious here using getattr and get, since some funny things have happened with relation data
        # in the past.
        raw_data_dict = getattr(relations[0], "data", {}).get(relations[0].app)
        if not raw_data_dict:
            return None

        # The data in the databag is in format [str: json-string-of-dict].  Expand out those nested JSON dicts so we
        # have the full object
        raw_data_dict = {k: json.loads(v) for k, v in raw_data_dict.items()}

        return TempoApiAppData.model_validate(raw_data_dict)

    def _validate_relation_metadata(self):
        """Validate that the provided relation has the correct metadata for this endpoint."""
        charm_root = Path(__file__).absolute().parents[4]
        # Do not check charmcraft.yaml, just metadata.yaml.  metadata.yaml is the only file that will be present in a
        # running charm, and this validation step fails during unit testing anyway so we'll never need to look at
        # charmcraft.yaml
        try:
            yaml_raw = (charm_root / "metadata.yaml").read_text()
            relation = yaml.safe_load(yaml_raw)["requires"][self._relation_name]
        except KeyError as e:
            raise ValueError(
                f"Relation '{self._relation_name}' not found in metadata.yaml."
            ) from e

        if relation.get("limit", None) != 1:
            raise ValueError(
                "TempoApiRequirer is designed for relating to a single application and must be used with limit=1."
            )


class TempoApiProvider:
    """The provider side of the tempo-api relation."""

    def __init__(
        self,
        relation_mapping: RelationMapping,
        relation_name: str,
        app: Application,
    ):
        """Initialize the TempoApiProvider object.

        This object is for serializing and sending data to a relation that uses the tempo-api interface - it does
        not automatically observe any events for that relation.  It is up to the charm using this to call publish when
        it is appropriate to do so, typically on at least the charm's leader_elected event and this relation's
        relation_joined event.

        Args:
            relation_mapping: The RelationMapping of a charm (typically `self.model.relations` from within a charm
                              object).
            app: This application.
            relation_name: The name of the wrapped relation.
        """
        self._charm_relation_mapping = relation_mapping
        self._app = app
        self._relation_name = relation_name

    @property
    def relations(self):
        """Return the applications related to us under the monitored relation."""
        return self._charm_relation_mapping.get(self._relation_name, ())

    def publish(
        self,
        direct_url_http: Union[AnyHttpUrl, str],
        direct_url_grpc: Union[AnyHttpUrl, str],
        ingress_url_http: Optional[Union[AnyHttpUrl, str]] = None,
        ingress_url_grpc: Optional[Union[AnyHttpUrl, str]] = None,
    ):
        """Post tempo-api to all related applications.

        This method writes to the relation's app data bag, and thus should never be called by a unit that is not the
        leader otherwise ops will raise an exception.

        Args:
            direct_url_http: The cluster-internal URL at which this application can be reached for an http connection.
                             Typically, this is a Kubernetes FQDN like name.namespace.svc.cluster.local for
                             connecting to the tempo http api from inside the cluster, with scheme and maybe port.
            direct_url_grpc: The cluster-internal URL at which this application can be reached for a grpc connection.
                             Typically, this is a Kubernetes FQDN like name.namespace.svc.cluster.local for
                             connecting to the tempo grpc api from inside the cluster, with scheme.
            ingress_url_http: The non-internal URL at which this application can be reached for an http connection.
                              Typically, this is an ingress URL.
            ingress_url_grpc: The non-internal URL at which this application can be reached for a grpc connection.
                              Typically, this is an ingress URL.
        """
        data = TempoApiAppData(
            http=TempoApiUrls(
                direct_url=direct_url_http,
                ingress_url=ingress_url_http,
            ),
            grpc=TempoApiUrls(
                direct_url=direct_url_grpc,
                ingress_url=ingress_url_grpc,
            ),
        ).model_dump(mode="json", by_alias=True, exclude_defaults=True, round_trip=True)
        # Flatten any nested objects, since relation databags are str:str mappings
        data = {k: json.dumps(v) for k, v in data.items()}

        for relation in self.relations:
            databag = relation.data[self._app]
            databag.update(data)
