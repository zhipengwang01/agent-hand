# 卸载：注销计划任务 + 删桌面快捷方式。默认保留服务目录与数据（审计/证据）；
# 加 -Purge 连服务目录一并删除。
param([switch]$Purge)
$ServiceDir = Join-Path $env:USERPROFILE 'agent-hand'
Stop-ScheduledTask -TaskName 'AgentHand' -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName 'AgentHand' -Confirm:$false -ErrorAction SilentlyContinue
$desktop = [Environment]::GetFolderPath('Desktop')
foreach ($n in @('Disable-AgentHand.lnk', 'Enable-AgentHand.lnk', 'Edge-Debug-AgentHand.lnk')) {
    Remove-Item (Join-Path $desktop $n) -ErrorAction SilentlyContinue
}
if ($Purge) {
    Remove-Item $ServiceDir -Recurse -Force -ErrorAction SilentlyContinue
    Write-Host '已彻底卸载（含数据）。'
} else {
    Write-Host "已卸载任务与快捷方式；服务目录保留在 $ServiceDir（加 -Purge 可全删）。"
}
