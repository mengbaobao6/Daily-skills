param(
  [string]$AppKey,
  [string]$Code
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Alibaba OAuth Code Exchange"

function Get-PlainTextSecret($secure) {
  $ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
  try {
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
  } finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
  }
}

function New-HmacSha256Sign([string]$Raw, [string]$Secret) {
  $hmac = [Security.Cryptography.HMACSHA256]::new([Text.Encoding]::UTF8.GetBytes($Secret))
  try {
    return (($hmac.ComputeHash([Text.Encoding]::UTF8.GetBytes($Raw)) | ForEach-Object { $_.ToString("X2") }) -join "")
  } finally {
    $hmac.Dispose()
  }
}

if ([string]::IsNullOrWhiteSpace($AppKey)) {
  $AppKey = Read-Host "Enter AppKey"
}
if ([string]::IsNullOrWhiteSpace($Code)) {
  $Code = Read-Host "Enter OAuth code"
}

$secureSecret = Read-Host "Enter AppSecret" -AsSecureString
$AppSecret = Get-PlainTextSecret $secureSecret

$timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString()
$raw = "/auth/token/create" +
  "app_key" + $AppKey +
  "code" + $Code +
  "sign_method" + "sha256" +
  "timestamp" + $timestamp
$sign = New-HmacSha256Sign -Raw $raw -Secret $AppSecret

$body = @{
  app_key = $AppKey
  code = $Code
  timestamp = $timestamp
  sign_method = "sha256"
  sign = $sign
}

Write-Host "Exchanging code for token..."
$token = Invoke-RestMethod `
  -Method Post `
  -Uri "https://open-api.alibaba.com/rest/auth/token/create" `
  -Body $body `
  -ContentType "application/x-www-form-urlencoded; charset=UTF-8"

$saveDir = Join-Path $PSScriptRoot "tokens"
New-Item -ItemType Directory -Force -Path $saveDir | Out-Null
$savePath = Join-Path $saveDir ("token-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
$token | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $savePath -Encoding UTF8

Write-Host ""
Write-Host "Token saved to:"
Write-Host $savePath -ForegroundColor Green
Write-Host ""
Write-Host "Keep this file private. It contains access_token and refresh_token." -ForegroundColor Yellow
