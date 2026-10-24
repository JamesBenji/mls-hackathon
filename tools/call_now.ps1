param(
    [Parameter(Mandatory = $true)]
    [int]$PlanId,
    [Parameter(Mandatory = $true)]
    [string]$Phone,
    [string]$Server = 'nulkode@server.lan'
)

$ErrorActionPreference = 'Stop'
$request = @{
    plan_id = $PlanId
    phone = $Phone
} | ConvertTo-Json -Compress

# Transport only non-secret arguments. Credentials stay on the deployment.
$encoded = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($request))
$remote = @'
import base64, json, subprocess
args = json.loads(base64.b64decode('REQUEST_ARGUMENTS'))
names = subprocess.check_output([
    'docker', 'ps', '--filter', 'name=api-47fqolmow9q4barezzxexmeb',
    '--format', '{{.Names}}',
], text=True).splitlines()
if len(names) != 1:
    raise SystemExit('Expected exactly one Medicall API container')
subprocess.run([
    'docker', 'exec', names[0], 'python',
    '/usr/local/bin/guardian-entrypoint.py', 'python',
    '-m', 'scripts.call_now', '--plan-id', str(args['plan_id']),
    '--phone', args['phone'],
    '--approve-real-call',
], check=True)
'@
$remote = $remote.Replace('REQUEST_ARGUMENTS', $encoded)
$remote | ssh -o BatchMode=yes -o ConnectTimeout=8 $Server 'python3 -'
if ($LASTEXITCODE -ne 0) {
    throw 'Call was not confirmed. Check call history before retrying.'
}
