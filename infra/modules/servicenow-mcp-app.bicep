// ============================================================================
// Module: ServiceNow MCP Container App
// Runs the ServiceNow MCP server as a Container App. In passthrough mode,
// bearer tokens from APIM are forwarded directly to the ServiceNow API.
// ============================================================================

@description('Azure region')
param location string

@description('Resource tags')
param tags object = {}

@description('ACR login server')
param registryLoginServer string

@description('ACR name')
param registryName string

@description('Container Apps Environment resource ID')
param containerAppsEnvironmentId string

@description('ServiceNow instance URL (e.g., https://dev281447.service-now.com)')
param snInstanceUrl string = ''

@description('Application Insights connection string')
param appInsightsConnectionString string = ''

// Look up registry to get admin credentials
resource registry 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: registryName
}

// --- ServiceNow MCP Container App ---
resource snMcpApp 'Microsoft.App/containerApps@2024-03-01' = {
  name: 'ca-sn-mcp'
  location: location
  tags: union(tags, {
    'azd-service-name': 'servicenow-mcp'
  })
  properties: {
    managedEnvironmentId: containerAppsEnvironmentId
    configuration: {
      activeRevisionsMode: 'Single'
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
        allowInsecure: false
      }
      registries: [
        {
          server: registryLoginServer
          username: registry.listCredentials().username
          passwordSecretRef: 'acr-password'
        }
      ]
      secrets: [
        {
          name: 'acr-password'
          value: registry.listCredentials().passwords[0].value
        }
      ]
    }
    template: {
      containers: [
        {
          name: 'servicenow-mcp'
          image: 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'
          resources: {
            cpu: json('0.25')
            memory: '0.5Gi'
          }
          env: [
            {
              name: 'SN_INSTANCE_URL'
              value: snInstanceUrl
            }
            {
              name: 'APPLICATIONINSIGHTS_CONNECTION_STRING'
              value: appInsightsConnectionString
            }
            {
              name: 'OTEL_SERVICE_NAME'
              value: 'servicenow-mcp'
            }
          ]
        }
      ]
      scale: {
        minReplicas: 1
        maxReplicas: 3
      }
    }
  }
}

output snMcpFqdn string = snMcpApp.properties.configuration.ingress.fqdn
output snMcpAppName string = snMcpApp.name
