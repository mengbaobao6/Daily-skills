param(
  [string]$AppKey = "510912",
  [string]$TokenFile,
  [string]$GroupId,
  [string]$LocationType = "ALL_GROUP",
  [int]$PageSize = 50,
  [int]$PageNo = 1
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Alibaba Photobank Query"

function Get-PlainTextSecret($secure) {
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  }
}

function New-HmacSha256Sign([hashtable]$Params, [string]$Secret) {
  $raw = ""
  $Params.Keys | Sort-Object | ForEach-Object {
    $raw += $_ + [string]$Params[$_]
  }

  $hmac = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($Secret))
  try {
    return (($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($raw)) | ForEach-Object { $_.ToString("X2") }) -join "")
  } finally {
    $hmac.Dispose()
  }
}

function Invoke-AlibabaApi([string]$Method, [hashtable]$BizParams, [string]$AccessToken, [string]$Secret) {
  $req = @{
    app_key = $AppKey
    access_token = $AccessToken
    method = $Method
    timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString()
    sign_method = "sha256"
    simplify = "true"
  }

  foreach ($key in $BizParams.Keys) {
    if ($null -ne $BizParams[$key] -and [string]$BizParams[$key] -ne "") {
      $req[$key] = $BizParams[$key]
    }
  }

  $req["sign"] = New-HmacSha256Sign -Params $req -Secret $Secret

  return Invoke-RestMethod `
    -Method Post `
    -Uri "https://open-api.alibaba.com/sync" `
    -Body $req `
    -ContentType "application/x-www-form-urlencoded; charset=UTF-8"
}

$tokenDir = Join-Path $PSScriptRoot "tokens"
if ([string]::IsNullOrWhiteSpace($TokenFile)) {
  $latest = Get-ChildItem -LiteralPath $tokenDir -Filter "token-*.json" -ErrorAction Stop |
    Sort-Object LastWriteTime -Descending |
    Select-Object -First 1
  if (!$latest) {
    throw "No token file found in $tokenDir"
  }
  $TokenFile = $latest.FullName
}

$token = Get-Content -LiteralPath $TokenFile -Raw | ConvertFrom-Json
if (!$token.access_token) {
  throw "Token file does not contain access_token: $TokenFile"
}

$secureSecret = Read-Host "Enter AppSecret" -AsSecureString
$appSecret = Get-PlainTextSecret $secureSecret

$biz = @{
  location_type = $LocationType
  page_size = $PageSize.ToString()
  page_no = $PageNo.ToString()
}
if (![string]::IsNullOrWhiteSpace($GroupId)) {
  $biz["group_id"] = $GroupId
}

Write-Host "Querying photobank..."
$response = Invoke-AlibabaApi `
  -Method "alibaba.icbu.photobank.list" `
  -BizParams $biz `
  -AccessToken $token.access_token `
  -Secret $appSecret

$outDir = Join-Path $PSScriptRoot "photobank"
New-Item -ItemType Directory -Force -Path $outDir | Out-Null
$outFile = Join-Path $outDir ("photobank-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
$response | ConvertTo-Json -Depth 50 | Set-Content -LiteralPath $outFile -Encoding UTF8

Write-Host ""
Write-Host "Photobank response saved to:"
Write-Host $outFile -ForegroundColor Green
Write-Host ""
Write-Host "Done."
