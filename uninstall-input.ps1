# Stop the keyboard/mouse counter and remove it from startup (queue and state are kept).
# Run:  powershell -ExecutionPolicy Bypass -File uninstall-input.ps1
$shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "piggy-input.lnk"
Remove-Item $shortcut -ErrorAction SilentlyContinue
Get-CimInstance Win32_Process |
  Where-Object { $_.CommandLine -like "*piggy_input.py*" } |
  ForEach-Object { Invoke-CimMethod -InputObject $_ -MethodName Terminate | Out-Null }
Write-Host "piggy-input stopped and removed from startup."
