param(
    [Parameter(Mandatory = $true)]
    [string]$InputFile,

    [Parameter(Mandatory = $true)]
    [string]$WorkDir
)

$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$inputPath = (Resolve-Path -LiteralPath $InputFile).Path
$workPath = [System.IO.Path]::GetFullPath($WorkDir)
$manifestPath = Join-Path $workPath 'compiled-batch.json'

New-Item -ItemType Directory -Path $workPath -Force | Out-Null
& python (Join-Path $scriptDir 'intake_tool.py') --input $inputPath --output $manifestPath
if ($LASTEXITCODE -ne 0) {
    throw "Input compilation failed with exit code $LASTEXITCODE."
}

& python (Join-Path $scriptDir 'batch_tool.py') prepare --manifest $manifestPath --work-dir $workPath
exit $LASTEXITCODE
