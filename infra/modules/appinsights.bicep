// Application Insights (workspace-based) + Log Analytics workspace.

@description('Resource name prefix.')
param prefix string

@description('Azure region.')
param location string

@description('Retention in days for Log Analytics.')
param retentionInDays int = 30

var workspaceName = 'log-${prefix}'
var appiName = 'appi-${prefix}'

resource workspace 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: workspaceName
  location: location
  properties: {
    sku: {
      name: 'PerGB2018'
    }
    retentionInDays: retentionInDays
    features: {
      enableLogAccessUsingOnlyResourcePermissions: true
    }
  }
}

resource appi 'Microsoft.Insights/components@2020-02-02' = {
  name: appiName
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: workspace.id
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

output workspaceId string = workspace.id
output appInsightsName string = appi.name
output appInsightsId string = appi.id
output connectionString string = appi.properties.ConnectionString
output instrumentationKey string = appi.properties.InstrumentationKey
