// ============================================================================
// Module: ServiceNow OBO Connection (RemoteTool with UserEntraToken auth)
// Creates a RemoteTool connection on the Foundry project that passes the user's
// Azure AD token through to APIM. APIM handles the token exchange to ServiceNow
// via JWT Bearer flow -- the connection itself does no OAuth consent.
// ============================================================================

@description('Name of the Cognitive Services account')
param cognitiveAccountName string

@description('Name of the AI Foundry project (child of cognitive account)')
param projectName string

@description('ServiceNow MCP OBO endpoint URL via APIM')
param snMcpOboEndpoint string

resource cognitiveAccount 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' existing = {
  name: cognitiveAccountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' existing = {
  parent: cognitiveAccount
  name: projectName
}

resource snOboConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-04-01-preview' = {
  parent: project
  name: 'servicenow-obo'
  properties: {
    authType: 'UserEntraToken'
    category: 'RemoteTool'
    target: snMcpOboEndpoint
    audience: 'https://ai.azure.com'
    metadata: {
      type: 'custom_MCP'
    }
    isSharedToAll: true
  }
}

@description('Name of the SN OBO connection')
output connectionName string = snOboConnection.name
