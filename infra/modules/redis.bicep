// Azure Cache for Redis. Basic in dev; Standard in staging; Premium + AOF in prod.

@description('Resource name prefix.')
param prefix string

@description('Azure region.')
param location string

@allowed([
  'Basic'
  'Standard'
  'Premium'
])
param skuName string = 'Basic'

@description('Capacity tier (e.g. 0 for C0, 1 for C1, 1 for P1).')
param capacity int = 0

@description('Enable AOF persistence (Premium only). Required for prod per AGENTS.md §4 rule #4.')
param enableAof bool = false

var redisName = 'redis-${prefix}'

var redisConfig = enableAof ? {
  'aof-backup-enabled': 'true'
} : {}

resource redis 'Microsoft.Cache/redis@2024-03-01' = {
  name: redisName
  location: location
  properties: {
    sku: {
      name: skuName
      family: skuName == 'Premium' ? 'P' : 'C'
      capacity: capacity
    }
    enableNonSslPort: false
    minimumTlsVersion: '1.2'
    redisConfiguration: redisConfig
    publicNetworkAccess: 'Enabled'
  }
}

output hostName string = redis.properties.hostName
output resourceId string = redis.id
output sslPort int = redis.properties.sslPort
