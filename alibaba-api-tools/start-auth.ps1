param(
  [string]$AppKey,
  [string]$AppSecret,
  [int]$Port = 8765
)

$ErrorActionPreference = "Stop"
$Host.UI.RawUI.WindowTitle = "Alibaba OAuth Token Tool"
$StatusPath = Join-Path $PSScriptRoot "auth-status.txt"

function Set-Status($message) {
  $line = "$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') $message"
  $line | Set-Content -LiteralPath $StatusPath -Encoding UTF8
}

function Write-Step($message) {
  Write-Host ""
  Write-Host "== $message ==" -ForegroundColor Cyan
  Set-Status $message
}

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

function Start-LocalTunnel([int]$LocalPort) {
  $npx = (Get-Command npx -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (!$npx) {
    throw "npx was not found. Please install Node.js or use another HTTPS tunnel tool."
  }

  $outLog = Join-Path $env:TEMP "alibaba-localtunnel.out.log"
  $errLog = Join-Path $env:TEMP "alibaba-localtunnel.err.log"
  Remove-Item -LiteralPath $outLog,$errLog -Force -ErrorAction SilentlyContinue

  $proc = Start-Process `
    -FilePath "powershell.exe" `
    -ArgumentList @("-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", "npx --yes localtunnel --port $LocalPort") `
    -RedirectStandardOutput $outLog `
    -RedirectStandardError $errLog `
    -WindowStyle Hidden `
    -PassThru

  $deadline = (Get-Date).AddSeconds(45)
  $publicUrl = $null
  while ((Get-Date) -lt $deadline) {
    Start-Sleep -Milliseconds 800
    $text = ""
    if (Test-Path -LiteralPath $outLog) { $text += Get-Content -LiteralPath $outLog -Raw -ErrorAction SilentlyContinue }
    if (Test-Path -LiteralPath $errLog) { $text += "`n" + (Get-Content -LiteralPath $errLog -Raw -ErrorAction SilentlyContinue) }
    $match = [regex]::Match($text, "https://[a-zA-Z0-9-]+\.loca\.lt")
    if ($match.Success) {
      $publicUrl = $match.Value.TrimEnd("/")
      break
    }
    if ($proc.HasExited) {
      throw "Tunnel process exited early. Log: $text"
    }
  }

  if (!$publicUrl) {
    try { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue } catch {}
    throw "Could not get a temporary HTTPS callback URL. Try again or use another tunnel tool."
  }

  return [pscustomobject]@{
    Process = $proc
    Url = $publicUrl
    OutLog = $outLog
    ErrLog = $errLog
  }
}

function Receive-OAuthCallback([int]$LocalPort) {
  $listener = [Net.Sockets.TcpListener]::new([Net.IPAddress]::Any, $LocalPort)
  $listener.Start()
  Write-Host "Waiting for Alibaba callback on port $LocalPort ..."
  try {
    while ($true) {
      $client = $listener.AcceptTcpClient()
      try {
        $client.ReceiveTimeout = 3000
        $client.SendTimeout = 3000
        $stream = $client.GetStream()
        $stream.ReadTimeout = 3000
        $stream.WriteTimeout = 3000
        $reader = [IO.StreamReader]::new($stream, [Text.Encoding]::ASCII, $false, 4096, $true)
        $requestLine = $reader.ReadLine()

        if ([string]::IsNullOrWhiteSpace($requestLine)) {
          $client.Close()
          continue
        }

        $parts = $requestLine -split " "
        if ($parts.Length -lt 2) {
          $client.Close()
          continue
        }

        $localUri = [Uri]::new("http://localhost$($parts[1])")
        $query = [Web.HttpUtility]::ParseQueryString($localUri.Query)
        $code = $query["code"]
        $errorValue = $query["error"]
        $errorDescription = $query["error_description"]
        $hasResult = ![string]::IsNullOrWhiteSpace($code) -or ![string]::IsNullOrWhiteSpace($errorValue)

        $ok = [string]::IsNullOrWhiteSpace($errorValue) -and ![string]::IsNullOrWhiteSpace($code)
        if ($ok) {
          $html = '<html><head><meta charset="utf-8"></head><body><h1>授权成功</h1><p>已收到 code，可以关闭此页面。</p></body></html>'
        } elseif ($hasResult) {
          $html = '<html><head><meta charset="utf-8"></head><body><h1>授权失败</h1><p>请回到工具窗口查看错误。</p></body></html>'
        } else {
          $html = '<html><head><meta charset="utf-8"></head><body><h1>OAuth listener is running</h1></body></html>'
        }

        $bodyBytes = [Text.Encoding]::UTF8.GetBytes($html)
        $header = "HTTP/1.1 200 OK`r`nContent-Type: text/html; charset=utf-8`r`nContent-Length: $($bodyBytes.Length)`r`nConnection: close`r`n`r`n"
        $headerBytes = [Text.Encoding]::ASCII.GetBytes($header)
        $stream.Write($headerBytes, 0, $headerBytes.Length)
        $stream.Write($bodyBytes, 0, $bodyBytes.Length)
        $stream.Flush()

        if ($ok) {
          $reader.Dispose()
          $client.Close()
          return $code
        }

        if ($hasResult) {
          throw "OAuth callback did not contain a code. error=$errorValue error_description=$errorDescription"
        }
      } catch [IO.IOException] {
        continue
      } finally {
        if ($reader) { $reader.Dispose() }
        if ($client) { $client.Close() }
      }
    }
  } finally {
    $listener.Stop()
  }
}

function Request-Token([string]$Key, [string]$Secret, [string]$Code) {
  $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds().ToString()
  $raw = "/auth/token/create" +
    "app_key" + $Key +
    "code" + $Code +
    "sign_method" + "sha256" +
    "timestamp" + $timestamp
  $sign = New-HmacSha256Sign -Raw $raw -Secret $Secret

  $body = @{
    app_key = $Key
    code = $Code
    timestamp = $timestamp
    sign_method = "sha256"
    sign = $sign
  }

  return Invoke-RestMethod `
    -Method Post `
    -Uri "https://open-api.alibaba.com/rest/auth/token/create" `
    -Body $body `
    -ContentType "application/x-www-form-urlencoded; charset=UTF-8"
}

Write-Step "Alibaba International OAuth"
Write-Host "This window will help bind your Alibaba app and get tokens."
Write-Host "Do not send AppSecret or token values to anyone."

if ([string]::IsNullOrWhiteSpace($AppKey)) {
  Set-Status "Waiting for AppKey input"
  $AppKey = Read-Host "Enter AppKey"
}
if ([string]::IsNullOrWhiteSpace($AppSecret)) {
  Set-Status "Waiting for AppSecret input"
  $secureSecret = Read-Host "Enter AppSecret" -AsSecureString
  $AppSecret = Get-PlainTextSecret $secureSecret
}

Write-Step "Starting temporary HTTPS callback"
$tunnel = Start-LocalTunnel -LocalPort $Port
$callbackUrl = "$($tunnel.Url)/alibaba/callback"
$encodedCallback = [uri]::EscapeDataString($callbackUrl)
$authUrl = "https://open-api.alibaba.com/oauth/authorize?response_type=code&force_auth=true&client_id=$AppKey&redirect_uri=$encodedCallback"

Write-Host "Callback URL to paste into Alibaba app settings:" -ForegroundColor Yellow
Write-Host $callbackUrl -ForegroundColor Green
$callbackUrl | Set-Content -LiteralPath (Join-Path $PSScriptRoot "callback-url.txt") -Encoding UTF8
Write-Host ""
Write-Host "After saving the exact callback URL in Alibaba Open Platform, press Enter here."
Set-Status "Waiting for callback URL to be saved in Alibaba app settings"
Read-Host | Out-Null

Write-Step "Opening authorization page"
Write-Host "Authorization URL:"
Write-Host $authUrl -ForegroundColor Green
Start-Process $authUrl

try {
  Set-Status "Waiting for browser authorization callback"
  $code = Receive-OAuthCallback -LocalPort $Port
  Write-Step "Exchanging code for token"
  $token = Request-Token -Key $AppKey -Secret $AppSecret -Code $code

  $saveDir = Join-Path $PSScriptRoot "tokens"
  New-Item -ItemType Directory -Force -Path $saveDir | Out-Null
  $savePath = Join-Path $saveDir ("token-" + (Get-Date -Format "yyyyMMdd-HHmmss") + ".json")
  $token | ConvertTo-Json -Depth 20 | Set-Content -LiteralPath $savePath -Encoding UTF8

  Write-Step "Done"
  Write-Host "Token saved to:"
  Write-Host $savePath -ForegroundColor Green
  Write-Host ""
  Write-Host "Keep this file private. It contains access_token and refresh_token." -ForegroundColor Yellow
  Set-Status "Token saved to $savePath"
} finally {
  if ($tunnel -and $tunnel.Process -and !$tunnel.Process.HasExited) {
    Stop-Process -Id $tunnel.Process.Id -Force -ErrorAction SilentlyContinue
  }
}
