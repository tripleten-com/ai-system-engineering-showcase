# TripleTen Cloud Platform - Autonomous Incident Defense Smoke Test (PowerShell Parity)
$ErrorActionPreference = "Continue"

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "TripleTen Cloud Platform - Autonomous Incident Defense Smoke Test" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

$FailedChecks = 0

function Test-Endpoint {
    param([string]$Name, [string]$Url)
    Write-Host -NoNewline "Checking $Name ($Url)... "
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200) {
            Write-Host "OK" -ForegroundColor Green
        } else {
            Write-Host ("FAILED (Status " + $response.StatusCode + ")") -ForegroundColor Red
            $script:FailedChecks++
        }
    } catch {
        Write-Host ("FAILED (" + $_.Exception.Message + ")") -ForegroundColor Red
        $script:FailedChecks++
    }
}

# 1. Container HTTP Health Endpoints
Test-Endpoint "incident-war-room" "http://localhost:3000"
Test-Endpoint "incident-agent-api healthz" "http://localhost:8000/healthz"
Test-Endpoint "grafana" "http://localhost:3001/api/health"
Test-Endpoint "prometheus" "http://localhost:9090/-/healthy"
Test-Endpoint "jaeger" "http://localhost:16686/"
Test-Endpoint "localstack" "http://localhost:4566/_localstack/health"

# 2. Redis check
Write-Host -NoNewline "Checking redis ping... "
try {
    $redisPing = docker exec redis redis-cli ping
    if ($redisPing -match "PONG") {
        Write-Host "OK" -ForegroundColor Green
    } else {
        Write-Host "FAILED" -ForegroundColor Red
        $FailedChecks++
    }
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    $FailedChecks++
}

# 3. PostgreSQL Schema, Indexes & Seed Runbooks Check
Write-Host -NoNewline "Checking postgres-vector connection, HNSW/GIN indexes & seed runbooks... "
try {
    $pgQuery = @"
SELECT 
    (SELECT count(*) FROM knowledge_runbooks WHERE id IN ('RB-104', 'RB-208', 'RB-312', 'SEC-501') AND embedding IS NOT NULL),
    (SELECT count(*) FROM pg_indexes WHERE tablename = 'knowledge_runbooks' AND indexname = 'idx_knowledge_runbooks_embedding'),
    (SELECT count(*) FROM pg_indexes WHERE tablename = 'knowledge_runbooks' AND indexname = 'idx_knowledge_runbooks_content_fts'),
    (SELECT count(*) FROM information_schema.tables WHERE table_name = 'checkpoints');
"@
    $pgRes = docker exec postgres-vector psql -U postgres -d tripleten_db -t -c $pgQuery
    $parts = ($pgRes.Trim() -split "\s+\|\s+|\s+") | Where-Object { $_ -ne "" }
    $rbCount = [int]$parts[0]
    $hnswCount = [int]$parts[1]
    $ginCount = [int]$parts[2]
    $chkCount = [int]$parts[3]

    if ($rbCount -ge 4 -and $hnswCount -ge 1 -and $ginCount -ge 1 -and $chkCount -ge 1) {
        Write-Host "OK: 4 runbooks, HNSW + GIN indexes, checkpointer verified" -ForegroundColor Green
    } else {
        Write-Host "FAILED (runbooks: $rbCount, hnsw: $hnswCount, gin: $ginCount, checkpointer: $chkCount)" -ForegroundColor Red
        $FailedChecks++
    }
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    $FailedChecks++
}

# 4. LocalStack SQS Queues, Redrive Policies & S3 Bucket Check
#
# Values are pulled with --query rather than matched against formatted JSON. The CLI
# renders RedrivePolicy as an escaped JSON *string*, and matching that text has already
# produced two false failures against a correctly provisioned stack. --query returns the
# unescaped value, so the comparison does not depend on quoting or whitespace at all.
# Kept behaviourally identical to the bash validator, including the failure text.
Write-Host -NoNewline "Checking LocalStack SQS queues, DLQ redrive policies & S3 bucket... "
$sqsReasons = @()

function Test-SourceQueue {
    param([string]$QueueName, [string]$DlqName)
    $reasons = @()
    $url = (docker exec localstack awslocal sqs get-queue-url --queue-name $QueueName --output text 2>$null)
    if ($null -ne $url) { $url = ([string]$url).Trim() }
    if ([string]::IsNullOrWhiteSpace($url)) {
        return @("url-unresolved:$QueueName")
    }
    $vis = (docker exec localstack awslocal sqs get-queue-attributes --queue-url $url `
        --attribute-names VisibilityTimeout --query 'Attributes.VisibilityTimeout' --output text 2>$null)
    $redrive = (docker exec localstack awslocal sqs get-queue-attributes --queue-url $url `
        --attribute-names RedrivePolicy --query 'Attributes.RedrivePolicy' --output text 2>$null)
    if ($null -ne $vis) { $vis = ([string]$vis).Trim() }
    if ($null -ne $redrive) { $redrive = ([string]$redrive).Trim() }

    if ($vis -ne "30") { $reasons += "${QueueName}:VisibilityTimeout=[$vis]want30" }
    if ($redrive -notlike "*$DlqName*") { $reasons += "${QueueName}:RedrivePolicy=[$redrive]want$DlqName" }
    if (-not ($redrive -match '"maxReceiveCount":\s*3')) { $reasons += "${QueueName}:maxReceiveCount=[$redrive]want3" }
    return $reasons
}

try {
    $queues = (docker exec localstack awslocal sqs list-queues --output text 2>$null | Out-String)
    $s3 = (docker exec localstack awslocal s3 ls 2>$null | Out-String)

    foreach ($q in @("customer-jobs", "customer-dlq", "remediation-jobs", "remediation-dlq")) {
        if ($queues -notlike "*$q*") { $sqsReasons += "queue-missing:$q" }
    }
    if ($s3 -notlike "*tripleten-cloud-postmortems*") {
        $sqsReasons += "bucket-missing:tripleten-cloud-postmortems"
    }

    $sqsReasons += Test-SourceQueue -QueueName "customer-jobs" -DlqName "customer-dlq"
    $sqsReasons += Test-SourceQueue -QueueName "remediation-jobs" -DlqName "remediation-dlq"

    if ($sqsReasons.Count -eq 0) {
        Write-Host "OK: queues, DLQs, 30s visibility & maxReceiveCount=3 verified" -ForegroundColor Green
    } else {
        # Print what was actually observed. A bare "mismatch" costs a whole CI cycle to diagnose.
        Write-Host ("FAILED - " + ($sqsReasons -join " ")) -ForegroundColor Red
        $FailedChecks++
    }
} catch {
    Write-Host "FAILED - exception: $_" -ForegroundColor Red
    $FailedChecks++
}

# 5. Worker heartbeat check in Redis
Write-Host -NoNewline "Checking remediation-worker heartbeat... "
try {
    $hb = docker exec redis redis-cli get "worker:heartbeat"
    if ($hb -and $hb -match "healthy") {
        Write-Host "OK" -ForegroundColor Green
    } else {
        Write-Host "FAILED" -ForegroundColor Red
        $FailedChecks++
    }
} catch {
    Write-Host "FAILED" -ForegroundColor Red
    $FailedChecks++
}

# 6. Prometheus scrape target & pre-provisioned Grafana dashboards
#
# Grafana answering /api/health above proves only that the process is alive. It would answer
# identically with zero dashboards and an unresolvable datasource, which is exactly what a
# broken provisioning change looks like. The dashboard roster is derived from Grafana rather
# than listed here, and the scrape target is checked for health=up, not merely for existence.
# Kept behaviourally identical to the bash validator, including the failure text.
Write-Host -NoNewline "Checking prometheus scrape target & provisioned Grafana dashboards... "
$obsReasons = @()

$basic = [Convert]::ToBase64String([Text.Encoding]::ASCII.GetBytes("admin:admin"))
$grafanaHeaders = @{ Authorization = "Basic $basic" }

try {
    $targets = (Invoke-WebRequest -Uri "http://localhost:9090/api/v1/targets?state=active" `
        -UseBasicParsing -TimeoutSec 5).Content
    if ($targets -notlike '*"health":"up"*') { $obsReasons += "scrape-target-not-up" }
    if ($targets -notlike '*incident-agent-api:8000/metrics*') {
        $obsReasons += "scrape-target-missing:incident-agent-api"
    }
} catch {
    $obsReasons += "scrape-target-not-up"
}

# The roster is derived, never listed. Grafana is asked which tripleten dashboards it loaded
# and the count is compared against the committed JSON files, so a sixth dashboard is checked
# the moment it lands. A hardcoded list would keep passing while silently covering five.
$dashDir = Join-Path $PSScriptRoot "..\infra\grafana\provisioning\dashboards"
$expectedDashboards = @(Get-ChildItem -Path $dashDir -Filter *.json -File -ErrorAction SilentlyContinue).Count
$foundUids = @()
try {
    $search = (Invoke-WebRequest -Uri "http://localhost:3001/api/search?tag=tripleten&type=dash-db" `
        -Headers $grafanaHeaders -UseBasicParsing -TimeoutSec 5).Content
    $foundUids = @([regex]::Matches($search, '"uid":"([^"]+)"') |
        ForEach-Object { $_.Groups[1].Value } | Sort-Object -Unique)
} catch {
    $foundUids = @()
}

if ($expectedDashboards -eq 0) {
    $obsReasons += "no-dashboard-json-found:$dashDir"
} elseif ($foundUids.Count -ne $expectedDashboards) {
    $obsReasons += ("dashboard-count=[" + $foundUids.Count + "]want" + $expectedDashboards)
}

foreach ($uid in $foundUids) {
    try {
        $dash = (Invoke-WebRequest -Uri "http://localhost:3001/api/dashboards/uid/$uid" `
            -Headers $grafanaHeaders -UseBasicParsing -TimeoutSec 5).Content
        if ($dash -notlike '*"provisioned":true*') { $obsReasons += "dashboard-not-provisioned:$uid" }
    } catch {
        $obsReasons += "dashboard-not-provisioned:$uid"
    }
}

$datasources = @("tripleten-prometheus", "tripleten-jaeger")
foreach ($ds in $datasources) {
    try {
        Invoke-WebRequest -Uri "http://localhost:3001/api/datasources/uid/$ds" `
            -Headers $grafanaHeaders -UseBasicParsing -TimeoutSec 5 | Out-Null
    } catch {
        $obsReasons += "datasource-missing:$ds"
    }
}

if ($obsReasons.Count -eq 0) {
    Write-Host ("OK: target up, " + $foundUids.Count + " dashboards provisioned, " + `
        $datasources.Count + " datasources resolved") -ForegroundColor Green
} else {
    Write-Host ("FAILED - " + ($obsReasons -join " ")) -ForegroundColor Red
    $FailedChecks++
}

Write-Host "=================================================================" -ForegroundColor Cyan
if ($FailedChecks -eq 0) {
    Write-Host "ALL SMOKE CHECKS PASSED (< 15s)" -ForegroundColor Green
    exit 0
} else {
    Write-Host "SMOKE CHECKS FAILED" -ForegroundColor Red
    exit 1
}
