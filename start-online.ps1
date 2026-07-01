# slurrrp — start the app AND a secure public link (Cloudflare Tunnel).
# Keep this window open while the cart is trading.
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$out = Join-Path $env:TEMP "slurrrp-tunnel-out.log"
$err = Join-Path $env:TEMP "slurrrp-tunnel-err.log"
Remove-Item $out, $err -ErrorAction SilentlyContinue

Write-Host "Starting slurrrp server..." -ForegroundColor Cyan
$server = Start-Process -FilePath "python" -ArgumentList "server.py", "8000" `
  -WorkingDirectory $root -WindowStyle Minimized -PassThru
Start-Sleep -Seconds 2

Write-Host "Opening secure public link (this can take ~15 seconds)..." -ForegroundColor Cyan
$cf = Join-Path $root "tools\cloudflared.exe"
$tunnel = Start-Process -FilePath $cf `
  -ArgumentList "tunnel", "--url", "http://localhost:8000", "--no-autoupdate", "--protocol", "http2" `
  -RedirectStandardOutput $out -RedirectStandardError $err -WindowStyle Hidden -PassThru

$url = $null
for ($i = 0; $i -lt 40; $i++) {
  Start-Sleep -Seconds 1
  foreach ($f in @($err, $out)) {
    if (Test-Path $f) {
      $m = Select-String -Path $f -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue | Select-Object -First 1
      if ($m) { $url = $m.Matches[0].Value; break }
    }
  }
  if ($url) { break }
}

Clear-Host
Write-Host ""
Write-Host "  ============================================================" -ForegroundColor DarkCyan
if ($url) {
  Write-Host "   slurrrp is ONLINE" -ForegroundColor Green
  Write-Host ""
  Write-Host "   Open this on the seller + kitchen phones (any network):" -ForegroundColor White
  Write-Host ""
  Write-Host "      $url" -ForegroundColor Yellow
  Write-Host ""
  Write-Host "   Log in:  admin / kitchen / seller   (password: slurrrp123)" -ForegroundColor Gray
} else {
  Write-Host "   Could not read the link automatically." -ForegroundColor Red
  Write-Host "   Check the file: $err" -ForegroundColor Red
}
Write-Host ""
Write-Host "   KEEP THIS WINDOW OPEN. Closing it takes slurrrp offline." -ForegroundColor Magenta
Write-Host "   To stop on purpose: run stop-slurrrp.bat" -ForegroundColor Gray
Write-Host "  ============================================================" -ForegroundColor DarkCyan
Write-Host ""

# Stay alive as long as the tunnel runs.
Wait-Process -Id $tunnel.Id
