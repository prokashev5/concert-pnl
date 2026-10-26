# Локальный запуск с ТЕСТОВЫМ ботом: туннель cloudflared + сервер в режиме polling.
# Адрес туннеля подхватывается сам — в .env нужен только BOT_TOKEN тестового бота.
# Запуск из корня проекта:  powershell -ExecutionPolicy Bypass -File scripts\dev.ps1
# Остановка: Ctrl+C (туннель закроется вместе с сервером).

$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

$port = 8000
$log = Join-Path $env:TEMP "concert-pnl-tunnel.log"
Remove-Item $log -ErrorAction SilentlyContinue

# cloudflared пишет адрес в stderr — перенаправляем в файл и ищем там
$tunnel = Start-Process cloudflared -ArgumentList "tunnel", "--url", "http://localhost:$port" `
    -RedirectStandardError $log -PassThru -NoNewWindow
try {
    Write-Host "Поднимаю туннель..."
    $url = $null
    for ($i = 0; $i -lt 60 -and -not $url; $i++) {
        Start-Sleep -Seconds 1
        if ($tunnel.HasExited) { throw "cloudflared завершился, лог: $log" }
        $match = Select-String -Path $log -Pattern "https://[a-z0-9-]+\.trycloudflare\.com" -ErrorAction SilentlyContinue |
            Select-Object -First 1
        if ($match) { $url = $match.Matches[0].Value }
    }
    if (-not $url) { throw "Туннель не выдал адрес за минуту, лог: $log" }

    Write-Host ""
    Write-Host "Mini App: $url" -ForegroundColor Green
    Write-Host "Пиши тестовому боту /start. Остановить: Ctrl+C" -ForegroundColor Green
    Write-Host ""

    # Переменные окружения важнее .env: адрес и режим берутся отсюда
    $env:WEBAPP_URL = $url
    $env:BOT_MODE = "polling"
    & .\.venv\Scripts\uvicorn.exe concert_pnl.api.app:create_app --factory --port $port
}
finally {
    Stop-Process -Id $tunnel.Id -ErrorAction SilentlyContinue
    Write-Host "Туннель закрыт."
}
