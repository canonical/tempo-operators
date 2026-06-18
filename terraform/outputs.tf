output "app_names" {
  value = merge(
    {
      tempo_s3_integrator     = juju_application.s3_integrator.name,
      tempo_coordinator       = module.tempo_coordinator.app_name,
      tempo_querier           = module.tempo_querier.app_name,
      tempo_query_frontend    = module.tempo_query_frontend.app_name,
      tempo_ingester          = module.tempo_ingester.app_name,
      tempo_distributor       = module.tempo_distributor.app_name,
      tempo_compactor         = module.tempo_compactor.app_name,
      tempo_metrics_generator = module.tempo_metrics_generator.app_name,
    }
  )
  description = "All application names which make up this product module"
}

output "provides" {
  value = {
    tempo_cluster     = module.tempo_coordinator.provides.tempo_cluster,
    grafana_dashboard = module.tempo_coordinator.provides.grafana_dashboard,
    grafana_source    = module.tempo_coordinator.provides.grafana_source,
    metrics_endpoint  = module.tempo_coordinator.provides.metrics_endpoint,
    provide_cmr_mesh  = module.tempo_coordinator.provides.provide_cmr_mesh,
    tracing           = module.tempo_coordinator.provides.tracing,
  }
  description = "All Juju integration endpoints where the charm is the provider"
}

output "requires" {
  value = {
    logging            = module.tempo_coordinator.requires.logging,
    ingress            = module.tempo_coordinator.requires.ingress,
    certificates       = module.tempo_coordinator.requires.certificates,
    send-remote-write  = module.tempo_coordinator.requires.send_remote_write,
    receive_datasource = module.tempo_coordinator.requires.receive_datasource,
    catalogue          = module.tempo_coordinator.requires.catalogue,
    require_cmr_mesh   = module.tempo_coordinator.requires.require_cmr_mesh,
    service_mesh       = module.tempo_coordinator.requires.service_mesh,
  }
  description = "All Juju integration endpoints where the charm is the requirer"
}
