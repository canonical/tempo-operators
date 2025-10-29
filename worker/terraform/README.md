# Terraform module for tempo-worker-k8s

This is a Terraform module facilitating the deployment of tempo-worker-k8s charm, using the [Terraform juju provider](https://github.com/juju/terraform-provider-juju/). For more information, refer to the provider [documentation](https://registry.terraform.io/providers/juju/juju/latest/docs). 

This module requires a `juju` model to be available. Refer to the [usage section](#usage) below for more details.

<!-- BEGIN_TF_DOCS -->
## Requirements

| Name | Version |
|------|---------|
| <a name="requirement_terraform"></a> [terraform](#requirement\_terraform) | >= 1.5 |
| <a name="requirement_juju"></a> [juju](#requirement\_juju) | ~> 1.0 |

## Providers

| Name | Version |
|------|---------|
| <a name="provider_juju"></a> [juju](#provider\_juju) | ~> 1.0 |

## Modules

No modules.

## Inputs

| Name | Description | Type | Default | Required |
|------|-------------|------|---------|:--------:|
| <a name="input_app_name"></a> [app\_name](#input\_app\_name) | Name to give the deployed application | `string` | `"tempo-worker"` | no |
| <a name="input_channel"></a> [channel](#input\_channel) | Channel that the charm is deployed from | `string` | n/a | yes |
| <a name="input_config"></a> [config](#input\_config) | Map of the charm configuration options | `map(string)` | `{}` | no |
| <a name="input_constraints"></a> [constraints](#input\_constraints) | String listing constraints for this application | `string` | `"arch=amd64"` | no |
| <a name="input_model_uuid"></a> [model\_uuid](#input\_model\_uuid) | Reference to an existing model resource or data source for the model to deploy to | `string` | n/a | yes |
| <a name="input_revision"></a> [revision](#input\_revision) | Revision number of the charm | `number` | `null` | no |
| <a name="input_storage_directives"></a> [storage\_directives](#input\_storage\_directives) | Map of storage used by the application, which defaults to 1 GB, allocated by Juju | `map(string)` | `{}` | no |
| <a name="input_units"></a> [units](#input\_units) | Unit count/scale | `number` | `1` | no |

## Outputs

| Name | Description |
|------|-------------|
| <a name="output_app_name"></a> [app\_name](#output\_app\_name) | n/a |
| <a name="output_endpoints"></a> [endpoints](#output\_endpoints) | n/a |
<!-- END_TF_DOCS -->

## Usage

> [!NOTE]
> This module is intended to be used only in conjunction with its counterpart, [Tempo coordinator module](https://github.com/canonical/tempo-coordinator-k8s-operator) and, when deployed in isolation, is not functional. 
> For the Tempo HA solution module deployment, check [Tempo HA module](https://github.com/canonical/observability)

### Basic usage
In order to deploy this standalone module, create a `main.tf` file with the following content:
```hcl
module "tempo-worker" {
  source      = "git::https://github.com/canonical/tempo-worker-k8s-operator//terraform"
  model_uuid  = var.model_uuid
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
  default     = "tempo-worker"
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
variable "model_uuid" {
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
terraform apply -var="model_uuid=<MODEL_UUID>" -auto-approve
```

### Deploy with constraints

In order to deploy this module with a set of constraints (e.g: architecture, anti-affinity rules, etc.), create a `main.tf` similar to the [basic usage `main.tf` file](#basic-usage). 

Then, create a `constraints.tfvars` file with the following content:
```hcl
model_uuid = <model-uuid>
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