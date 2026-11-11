# AgentHand 部署脚本（在目标机以当前用户运行，不需管理员）
# 做五件事：查 Python/Pillow → 拷服务文件+生成 config → 生成无窗口包装
#          → 注册计划任务（四件套）→ 启动并验心跳
# 计划任务四件套（历史教训钉死）：①pythonw 无窗口直启（原 wscript 包装真机挂死，见第 4 节注） ②AllowStartIfOnBatteries
#   ③DontStopIfGoingOnBatteries ④UserId = COMPUTERNAME\当前用户
$ErrorActionPreference = 'Stop'

$PkgRoot    = Split-Path -Parent $PSScriptRoot          # 包根（deploy 的上级）
$ServiceDir = Join-Path $env:USERPROFILE 'agent-hand'
$TaskName   = 'AgentHand'
Write-Host "== AgentHand部署 =="
Write-Host "服务目录: $ServiceDir"

# --- 1. 查 Python（需要 pythonw.exe 无窗口解释器） ---
$py = $null
foreach ($cand in @('python', 'py')) {
    $c = Get-Command $cand -ErrorAction SilentlyContinue
    if ($c) { $py = $c.Source; break }
}
if (-not $py) {
    Write-Host '未找到 Python。请先离线安装 Python 3.11+（勾选 Add to PATH），再重跑本脚本。' -ForegroundColor Red
    exit 1
}
$pyw = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }   # 兜底：无 pythonw 时退回 python（会有窗口，仅应急）
Write-Host "Python: $py"

# --- 2. 查 Pillow（抓屏依赖；缺失时给离线装法，不自动联网装） ---
& $py -c "import PIL" 2>$null
if ($LASTEXITCODE -ne 0) {
    Write-Host '缺少 Pillow。在有网机器上: pip download pillow -d wheels，把 wheels 拷到本机后: pip install --no-index --find-links wheels pillow' -ForegroundColor Red
    exit 2
}

# --- 3. 拷文件 + 生成 config（含随机 token，UTF-8 无 BOM 写入） ---
New-Item -ItemType Directory -Force -Path $ServiceDir | Out-Null
Copy-Item (Join-Path $PkgRoot 'server\*.py') $ServiceDir -Force
$configPath = Join-Path $ServiceDir 'config.json'
if (-not (Test-Path $configPath)) {
    $token = [guid]::NewGuid().ToString('N')
    $cfg = [ordered]@{
        bind_host = 'auto'; port = 18765; token = $token
        observe_only = $true; stream = $true
        data_dir = 'data'; heartbeat_interval = 2; evidence_keep_days = 7
    } | ConvertTo-Json
    [System.IO.File]::WriteAllText($configPath, $cfg, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "已生成 config.json，调用 token: $token（请记给云端调用端）"
}

# --- 4. 计划任务动作：pythonw 无窗口直启 ---
# 注：原设计为 wscript+VBS 包装，2026-11-11 笔记本真机实测 wscript 挂死不拉起
# 服务（进程悬停、无日志无心跳），改 pythonw 直启一次通过。pythonw 本身无控制台
# 窗口，与「无黑框」目标等效；其余三件套（触发器/电池两项/UserId）不变。

# --- 5. 注册计划任务（四件套） ---
$action    = New-ScheduledTaskAction -Execute $pyw -Argument "`"$(Join-Path $ServiceDir 'agent_hand.py')`"" -WorkingDirectory $ServiceDir
$trigger   = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$principal = New-ScheduledTaskPrincipal -UserId "$env:COMPUTERNAME\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$settings  = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Days 365)
Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Force | Out-Null
Write-Host "计划任务 $TaskName 已注册"

# --- 6. 桌面开关（Disable/Enable，一键停手） ---
Copy-Item (Join-Path $PSScriptRoot 'disable.ps1') $ServiceDir -Force
Copy-Item (Join-Path $PSScriptRoot 'enable.ps1')  $ServiceDir -Force
$ws = New-Object -ComObject WScript.Shell
foreach ($pair in @(@('Disable-AgentHand', 'disable.ps1'), @('Enable-AgentHand', 'enable.ps1'))) {
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) ($pair[0] + '.lnk')))
    $lnk.TargetPath = 'powershell.exe'
    $lnk.Arguments  = "-NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $ServiceDir $pair[1])`""
    $lnk.Save()
}

# --- 7. 附带：Edge 调试模式快捷方式（独立 profile，不动日常 Edge；首次需在该窗口登录 claude.ai） ---
$edge = @("$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe", "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe") |
        Where-Object { Test-Path $_ } | Select-Object -First 1
if ($edge) {
    $lnk = $ws.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Desktop')) 'Edge-Debug-AgentHand.lnk'))
    $lnk.TargetPath = $edge
    $lnk.Arguments  = "--remote-debugging-port=9222 --user-data-dir=`"$env:USERPROFILE\agent-hand\edge-debug-profile`""
    $lnk.Save()
    Write-Host '已建桌面快捷方式 Edge-Debug-AgentHand（CDP 线专用，独立 profile）'
}

# --- 8. 启动并验心跳 ---
Start-ScheduledTask -TaskName $TaskName
Start-Sleep -Seconds 4
$hb = Join-Path $ServiceDir 'data\heartbeat.json'
if (Test-Path $hb) {
    Write-Host '心跳已落盘，部署成功:' -ForegroundColor Green
    Get-Content $hb -Raw
    Write-Host "`n状态页: http://<本机Tailscale IP>:18765/ （observe_only 默认为 true，验收通过后再改 false 放行动作）"
} else {
    Write-Host "未见心跳文件，请查 $ServiceDir\data\service.log" -ForegroundColor Red
    exit 3
}
