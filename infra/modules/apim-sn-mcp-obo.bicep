// ============================================================================
// Module: APIM ServiceNow MCP OBO (native MCP type)
// Uses APIM's native 'mcp' API type with a backend resource pointing to the
// SN MCP Container App. Azure AD token validation and JWT Bearer OBO exchange.
//
// Simplified vs. SF: ServiceNow's user_field=email allows direct mapping from
// Azure AD preferred_username -- no service account or user lookup needed.
//
// Includes RFC 9728 Protected Resource Metadata (PRM) endpoint advertising
// Azure AD as the authorization server.
// ============================================================================

@description('Name of the existing API Management instance')
param apimName string

@description('ServiceNow MCP Container App FQDN')
param snMcpFqdn string

@description('ServiceNow OAuth client ID for OBO (from oauth_jwt app)')
param snOboClientId string = 'placeholder-updated-by-hook'

@description('ServiceNow instance URL for JWT Bearer token exchange')
param snOboInstanceUrl string = 'https://dev281447.service-now.com'

@description('Thumbprint of the SN JWT Bearer signing certificate in APIM')
param snJwtBearerCertThumbprint string = ''

@description('kid from jwt_verifier_map in ServiceNow')
param snJwtBearerKid string = ''

// --------------------------------------------------------------------------
// Reference existing APIM instance
// --------------------------------------------------------------------------
resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

// --------------------------------------------------------------------------
// Named Values for OBO policies
// APIMGatewayURL and TenantId are owned by the SF deployment -- reference as existing.
// Create new: SN-specific values only.
// --------------------------------------------------------------------------
resource apimGatewayUrlNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' existing = {
  parent: apim
  name: 'APIMGatewayURL'
}

resource tenantIdNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' existing = {
  parent: apim
  name: 'TenantId'
}

resource snOboClientIdNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'SnOboClientId'
  properties: {
    displayName: 'SnOboClientId'
    value: snOboClientId
    secret: false
  }
}

resource snOboInstanceUrlNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'SnOboInstanceUrl'
  properties: {
    displayName: 'SnOboInstanceUrl'
    value: snOboInstanceUrl
    secret: false
  }
}

resource snJwtBearerCertThumbprintNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'SnJwtBearerCertThumbprint'
  properties: {
    displayName: 'SnJwtBearerCertThumbprint'
    value: !empty(snJwtBearerCertThumbprint) ? snJwtBearerCertThumbprint : 'placeholder-updated-by-hook'
    secret: false
  }
}

resource snJwtBearerKidNV 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'SnJwtBearerKid'
  properties: {
    displayName: 'SnJwtBearerKid'
    value: !empty(snJwtBearerKid) ? snJwtBearerKid : 'placeholder-updated-by-hook'
    secret: false
  }
}

// --------------------------------------------------------------------------
// Backend -- points APIM to the SN MCP Container App
// --------------------------------------------------------------------------
resource snMcpBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apim
  name: 'sn-mcp-backend'
  properties: {
    url: 'https://${snMcpFqdn}'
    protocol: 'http'
    title: 'ServiceNow MCP Server'
  }
}

// --------------------------------------------------------------------------
// ServiceNow MCP OBO API (native MCP type with OBO token exchange)
// --------------------------------------------------------------------------
resource snMcpOboApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'servicenow-mcp-obo'
  properties: {
    displayName: 'ServiceNow MCP Server (OBO)'
    description: 'Native MCP API with Azure AD -> SN JWT Bearer OBO exchange.'
    path: 'servicenow-mcp-obo'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    type: 'mcp'
    backendId: snMcpBackend.name
    mcpProperties: {
      endpoints: {
        mcp: {
          uriTemplate: '/mcp'
        }
      }
    }
  }
}

// --------------------------------------------------------------------------
// API-level policy (Azure AD validate + JWT Bearer exchange + cache)
// --------------------------------------------------------------------------
resource snMcpOboApiPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: snMcpOboApi
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/sn-mcp-obo-policy.xml')
  }
  dependsOn: [
    tenantIdNV
    snOboClientIdNV
    snOboInstanceUrlNV
    snJwtBearerCertThumbprintNV
    snJwtBearerKidNV
  ]
}

// --------------------------------------------------------------------------
// PRM endpoint (RFC 9728 Protected Resource Metadata -- anonymous access)
// Advertises Azure AD as authorization server (not ServiceNow)
// --------------------------------------------------------------------------
resource snOboPrmApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'servicenow-mcp-obo-prm'
  properties: {
    displayName: 'SN MCP OBO Protected Resource Metadata'
    path: 'servicenow-mcp-obo/.well-known'
    protocols: [
      'https'
    ]
    subscriptionRequired: false
    apiType: 'http'
  }
}

resource snOboPrmOp 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = {
  parent: snOboPrmApi
  name: 'sn-obo-oauth-protected-resource'
  properties: {
    displayName: 'SN OBO Protected Resource Metadata'
    method: 'GET'
    urlTemplate: '/oauth-protected-resource'
  }
}

resource snOboPrmOpPolicy 'Microsoft.ApiManagement/service/apis/operations/policies@2024-06-01-preview' = {
  parent: snOboPrmOp
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/sn-mcp-obo-prm-policy.xml')
  }
  dependsOn: [ tenantIdNV, apimGatewayUrlNV ]
}

// --------------------------------------------------------------------------
// Outputs
// --------------------------------------------------------------------------
@description('ServiceNow MCP OBO endpoint URL via APIM')
output snMcpOboEndpoint string = '${apim.properties.gatewayUrl}/servicenow-mcp-obo/mcp'
