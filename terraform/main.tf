resource "juju_application" "tempo_coordinator" {

  name = var.app_name
  # Coordinator and worker must be in the same model
  model       = var.model_name
  trust       = true
  units       = var.units
  config      = var.config
  constraints = var.constraints


  charm {
    name     = "tempo-coordinator-k8s"
    channel  = var.channel
    revision = var.revision
  }

}
