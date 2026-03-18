using './main.bicep'

param location = readEnvironmentVariable('AZURE_LOCATION', 'swedencentral')
param tags = {
  'azd-env-name': readEnvironmentVariable('AZURE_ENV_NAME', 'sn-mcp-tool')
  project: 'snow-meta-tool'
}

// Existing shared resource names (from rg-sf-mcp-obo)
param apimName = readEnvironmentVariable('APIM_NAME', 'apim-sf-mcp-obo')
param containerAppsEnvironmentName = readEnvironmentVariable('CONTAINER_APPS_ENV_NAME', 'cae-sf-mcp-obo')
param acrName = readEnvironmentVariable('AZURE_CONTAINER_REGISTRY_NAME', '')
param keyVaultName = readEnvironmentVariable('KEY_VAULT_NAME', 'kv-sf-mcp-obo')
param cognitiveAccountName = readEnvironmentVariable('COGNITIVE_ACCOUNT_NAME', '')
param aiProjectName = readEnvironmentVariable('AI_FOUNDRY_PROJECT_NAME', '')
param appInsightsName = readEnvironmentVariable('APP_INSIGHTS_NAME', 'appi-sf-mcp-obo')

// SN-specific parameters
param snInstanceUrl = readEnvironmentVariable('SN_INSTANCE_URL', '')
param snOboClientId = readEnvironmentVariable('SN_OAUTH_CLIENT_ID', 'placeholder-updated-by-hook')
param snJwtBearerKid = readEnvironmentVariable('SN_JWT_BEARER_KID', '')
param snJwtBearerCertName = readEnvironmentVariable('SN_JWT_BEARER_CERT_NAME', 'sn-jwt-bearer')
param snJwtBearerCertThumbprint = readEnvironmentVariable('SN_JWT_BEARER_CERT_THUMBPRINT', '')
