@description('Additive V3 Bot Service; not the legacy application bot')
param botName string = 'bot-servicenow-assistant-v3'

@description('Verified V3 agent instance_identity.client_id')
@minLength(36)
@maxLength(36)
param msaAppId string

@description('Azure AD tenant ID')
@minLength(36)
@maxLength(36)
param tenantId string

@description('Exact V3 agent Activity Protocol endpoint, including api-version=v1')
param endpoint string

resource botService 'Microsoft.BotService/botServices@2022-09-15' = {
  name: botName
  location: 'global'
  kind: 'azurebot'
  sku: {
    name: 'F0'
  }
  properties: {
    displayName: 'ServiceNow Assistant (V3)'
    endpoint: endpoint
    msaAppId: msaAppId
    msaAppTenantId: tenantId
    msaAppType: 'SingleTenant'
    publicNetworkAccess: 'Disabled'
  }
}

resource teamsChannel 'Microsoft.BotService/botServices/channels@2021-03-01' = {
  parent: botService
  name: 'MsTeamsChannel'
  location: 'global'
  properties: {
    channelName: 'MsTeamsChannel'
    properties: {
      isEnabled: true
      deploymentEnvironment: 'CommercialDeployment'
    }
  }
}

output botServiceName string = botService.name
output botServiceEndpoint string = botService.properties.endpoint
output botServiceId string = botService.id
