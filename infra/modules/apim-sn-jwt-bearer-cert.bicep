// ============================================================================
// Module: APIM SN JWT Bearer Certificate
// References the SN JWT Bearer signing certificate from Key Vault.
// Must be deployed AFTER APIM has "Key Vault Secrets User" role on the KV.
//
// Certificate is accessed in APIM policies via thumbprint:
//   context.Deployment.Certificates["{{SnJwtBearerCertThumbprint}}"]
// (context.Deployment.Certificates is keyed by thumbprint, NOT name)
// ============================================================================

@description('Name of the existing API Management instance')
param apimName string

@description('Key Vault URI (e.g., https://kv-name.vault.azure.net/)')
param keyVaultUri string

@description('Name of the certificate secret in Key Vault')
param snJwtBearerCertName string

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' existing = {
  name: apimName
}

resource snJwtBearerCert 'Microsoft.ApiManagement/service/certificates@2024-06-01-preview' = {
  parent: apim
  name: 'sn-jwt-bearer'
  properties: {
    keyVault: {
      secretIdentifier: '${keyVaultUri}secrets/${snJwtBearerCertName}'
    }
  }
}
