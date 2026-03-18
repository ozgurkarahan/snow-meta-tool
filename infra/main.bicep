targetScope = 'resourceGroup'

// ============================================================================
// ServiceNow MCP -- Standalone IaC
// Deploys SN-specific resources into the EXISTING rg-sf-mcp-obo resource group.
// References shared resources (APIM, Container Apps Env, ACR, KV, AI Foundry)
// that were provisioned by the Salesforce MCP project.
// ============================================================================

@description('Azure region for all resources')
param location string = 'swedencentral'

@description('Resource tags')
param tags object = {}

// --- Existing resource names (from SF deployment) ---
@description('Name of the existing APIM instance')
param apimName string

@description('Name of the existing Container Apps Environment')
param containerAppsEnvironmentName string

@description('Name of the existing ACR')
param acrName string

@description('Name of the existing Key Vault')
param keyVaultName string

@description('Name of the existing Cognitive Services account')
param cognitiveAccountName string

@description('Name of the existing AI Foundry project')
param aiProjectName string

@description('Name of the existing Application Insights')
param appInsightsName string

// --- SN-specific parameters ---
@description('ServiceNow instance URL (e.g., https://dev281447.service-now.com)')
param snInstanceUrl string = ''

@description('ServiceNow OAuth client ID (from oauth_jwt app)')
param snOboClientId string = 'placeholder-updated-by-hook'

@description('kid from jwt_verifier_map in ServiceNow')
param snJwtBearerKid string = ''

@description('Name of the SN JWT Bearer certificate in Key Vault')
param snJwtBearerCertName string = 'sn-jwt-bearer'

@description('Thumbprint of the SN JWT Bearer signing certificate (for APIM policy cert lookup)')
param snJwtBearerCertThumbprint string = ''

// ============================================================================
// Reference existing shared resources
// ============================================================================

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

resource containerEnv 'Microsoft.App/managedEnvironments@2024-03-01' existing = {
  name: containerAppsEnvironmentName
}

resource acr 'Microsoft.ContainerRegistry/registries@2023-11-01-preview' existing = {
  name: acrName
}

resource kv 'Microsoft.KeyVault/vaults@2023-07-01' existing = {
  name: keyVaultName
}

resource cognitive 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: cognitiveAccountName
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

// ============================================================================
// Tier 1: ServiceNow MCP Container App
// ============================================================================

module snMcpApp 'modules/servicenow-mcp-app.bicep' = {
  name: 'servicenow-mcp-app'
  params: {
    location: location
    tags: tags
    registryLoginServer: acr.properties.loginServer
    registryName: acr.name
    containerAppsEnvironmentId: containerEnv.id
    snInstanceUrl: snInstanceUrl
    appInsightsConnectionString: appInsights.properties.ConnectionString
  }
}

// ============================================================================
// Tier 2: APIM API + Backend + Policy (depends on Container App for FQDN)
// ============================================================================

module apimSnMcpObo 'modules/apim-sn-mcp-obo.bicep' = {
  name: 'apim-sn-mcp-obo'
  params: {
    apimName: apim.name
    snMcpFqdn: snMcpApp.outputs.snMcpFqdn
    snOboClientId: snOboClientId
    snOboInstanceUrl: !empty(snInstanceUrl) ? snInstanceUrl : 'https://dev281447.service-now.com'
    snJwtBearerCertThumbprint: snJwtBearerCertThumbprint
    snJwtBearerKid: snJwtBearerKid
  }
}

// ============================================================================
// Tier 3: APIM JWT Bearer Certificate (conditional on thumbprint)
// ============================================================================

module apimSnJwtBearerCert 'modules/apim-sn-jwt-bearer-cert.bicep' = if (!empty(snJwtBearerCertThumbprint)) {
  name: 'apim-sn-jwt-bearer-cert'
  params: {
    apimName: apim.name
    keyVaultUri: kv.properties.vaultUri
    snJwtBearerCertName: snJwtBearerCertName
  }
}

// ============================================================================
// Tier 4: Foundry Connection (depends on APIM endpoint)
// ============================================================================

module snOboConnection 'modules/sn-obo-connection.bicep' = {
  name: 'sn-obo-connection'
  params: {
    cognitiveAccountName: cognitive.name
    projectName: aiProjectName
    snMcpOboEndpoint: apimSnMcpObo.outputs.snMcpOboEndpoint
  }
}

// ============================================================================
// Outputs (become azd env vars)
// ============================================================================

output SN_MCP_CONTAINER_APP_NAME string = snMcpApp.outputs.snMcpAppName
output SN_MCP_FQDN string = snMcpApp.outputs.snMcpFqdn
output SN_OBO_CONNECTION_NAME string = snOboConnection.outputs.connectionName
output APIM_SN_MCP_OBO_ENDPOINT string = apimSnMcpObo.outputs.snMcpOboEndpoint
output APIM_NAME string = apim.name
output KEY_VAULT_NAME string = kv.name
output AZURE_CONTAINER_REGISTRY_NAME string = acr.name
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = acr.properties.loginServer
output APIM_GATEWAY_URL string = apim.properties.gatewayUrl
output COGNITIVE_ACCOUNT_NAME string = cognitive.name
output AI_FOUNDRY_PROJECT_NAME string = aiProjectName
output AZURE_RESOURCE_GROUP string = resourceGroup().name
