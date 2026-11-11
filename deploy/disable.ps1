# 一键停手：落 DISABLED 标志 + 停计划任务。心跳会如实显示 disabled（若服务还在跑）。
$ServiceDir = Join-Path $env:USERPROFILE 'agent-hand'
New-Item -ItemType File -Force -Path (Join-Path $ServiceDir 'DISABLED') | Out-Null
Stop-ScheduledTask -TaskName 'AgentHand' -ErrorAction SilentlyContinue
Write-Host 'AgentHand 已停用（DISABLED 标志已落、任务已停）。'
