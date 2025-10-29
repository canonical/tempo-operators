resource "juju_application" "tempo_coordinator" {
  name               = var.app_name
  config             = var.config
  constraints        = var.constraints
  model_uuid         = var.model_uuid
  storage_directives = var.storage_directives
  trust              = true
  units              = var.units

  charm {
    name     = "tempo-coordinator-k8s"
    channel  = var.channel
    revision = var.revision
  }
}
