# Запуск Рогнара «и забыть»: держит его живым и пишет лог.
#
# Слушатель может упасть по причинам, которые от него не зависят, — оборвалась
# сеть, Telegram придержал соединение, машина ушла в сон. Сам по себе он после
# этого не вернётся, поэтому поднимаем его в цикле.
#
#   .\rognar\start.ps1
#
# Остановить — Ctrl+C в этом окне (или закрыть окно).
# Лог пишется в rognar\rognar.log, туда же уходят ошибки.

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
$root = Split-Path -Parent $here
$log  = Join-Path $here "rognar.log"

Set-Location $root
$env:PYTHONIOENCODING = "utf-8"
$env:PYTHONUNBUFFERED = "1"

Write-Host "Рогнар запущен. Лог: $log"
Write-Host "Остановить — Ctrl+C.`n"

$attempt = 0
while ($true) {
    $attempt++
    $started = Get-Date
    "=== запуск #$attempt в $($started.ToString('dd.MM HH:mm:ss')) ===" | Tee-Object -FilePath $log -Append

    python rognar-rossa/rognar.py 2>&1 | Tee-Object -FilePath $log -Append

    $lived = [int]((Get-Date) - $started).TotalSeconds
    "=== завершился через $lived с ===" | Tee-Object -FilePath $log -Append

    # Если процесс упал сразу, значит дело не в сети, а в настройке: ключ,
    # сессия, занятый замок. Молотить перезапусками бессмысленно — подождём
    # подольше, чтобы человек успел прочитать ошибку в логе.
    $pause = if ($lived -lt 30) { 60 } else { 10 }
    Write-Host "`nРогнар остановился. Перезапуск через $pause с… (Ctrl+C — выйти)" -ForegroundColor Yellow
    Start-Sleep -Seconds $pause
}
