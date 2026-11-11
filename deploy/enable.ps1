# 恢复运行：删 DISABLED 标志 + 启动计划任务。
$ServiceDir = Join-Path $env:USERPROFILE 'agent-hand'
Remove-Item (Join-Path $ServiceDir 'DISABLED') -ErrorAction SilentlyContinue
Start-ScheduledTask -TaskName 'AgentHand'
Write-Host 'AgentHand 已启用。'
