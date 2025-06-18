Terraform module for tempo-coordinator-k8s

This is a Terraform module facilitating the deployment of tempo-coordinator-k8s charm, using the [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs). 


## Requirements
This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

## API

### Inputs
The module offers the following configurable inputs:

| Name | Type | Description | Required |
| - | - | - | - |
| `app_name`| string | Application name | tempo |
| `channel`| string | Channel that the charm is deployed from | latest/edge |
| `config`| map(any) | Map of the charm configuration options | {} |
| `model_name`| string | Name of the model that the charm is deployed on |  |
| `revision`| number | Revision number of the charm name |  |
| `units`| number | Number of units to deploy | 1 |
| `constraints`| string | String listing constraints for this application | arch=amd64 |

### Outputs
Upon applied, the module exports the following outputs:

| Name | Description |
| - | - |
| `app_name`|  Application name |
| `provides`| Map of `provides` endpoints |
| `requires`|  Map of `requires` endpoints |

## Usage

> [!NOTE]
> This module is intended to be used only in conjunction with its counterpart, [Tempo worker module](https://github.com/canonical/tempo-worker-k8s-operator) and, when deployed in isolation, is not functional. 
> For the Tempo HA solution module deployment, check [Tempo HA module](https://github.com/canonical/observability)
>
> Additionally, a Juju model must be ready in advance.

### Basic usage
In order to deploy this standalone module, create a `main.tf` file with the following content:

```hcl
module "tempo-coordinator" {

  source      = "git::https://github.com/canonical/tempo-coordinator-k8s-operator//terraform"
  model_name  = var.model_name
  app_name    = var.app_name
  channel     = var.channel
  config      = var.config
  revision    = var.revision
  units       = var.units
  constraints = var.constraints
}

variable "app_name" {
  description = "Application name"
  type        = string
  default     = "tempo"
}

variable "channel" {
  description = "Charm channel"
  type        = string
  default     = "latest/edge"
}

variable "config" {
  description = "Charm config options as in the ones we pass in juju config"
  type        = map(any)
  default     = {}
}

variable "model_name" {
  description = "Model name"
  type        = string
}

variable "revision" {
  description = "Charm revision"
  type        = number
  default     = null
}

variable "units" {
  description = "Number of units"
  type        = number
  default     = 1
}

variable "constraints" {
  description = "Constraints for the charm deployment"
  type        = string
  default     = "arch=amd64"
}

```
Then, use terraform to deploy the module:
```
terraform init
terraform apply -var="model_name=<MODEL_NAME>" -auto-approve
```

### Deploy with constraints

In order to deploy this module with a set of constraints (e.g: architecture, anti-affinity rules, etc.), create a `main.tf` similar to the [basic usage `main.tf` file](#basic-usage). 

Then, create a `constraints.tfvars` file with the following content:
```hcl
model_name = <model-name>
constraints = "arch=<desired-arch> mem=<desired-memory>"
```
> [!NOTE]
> See [Juju constraints](https://documentation.ubuntu.com/juju/latest/reference/constraint/#list-of-constraints) for a list of available juju constraints.

Then, use terraform to deploy the module:
```
terraform init
terraform apply -var-file=constraints.tfvars
```
> [!NOTE]
> Any constraints must be prepended with "`arch=<desired-arch> `" for Terraform operations to work.
>
> See [Juju Terraform provider issue](https://github.com/juju/terraform-provider-juju/issues/344)

