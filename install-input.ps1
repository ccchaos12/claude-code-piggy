# Install the keyboard/mouse counter on Windows and start it at login.
# Run:  powershell -ExecutionPolicy Bypass -File install-input.ps1
$ErrorActionPreference = "Stop"
$repo = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path $env:APPDATA "piggy-input\app"
New-Item -ItemType Directory -Force $app | Out-Null
Copy-Item (Join-Path $repo "piggy_hook.py"), (Join-Path $repo "piggy_input.py") $app -Force

# pythonw.exe runs without a console window.
$py = $null
foreach ($name in "pythonw.exe", "pyw.exe") {
  $cmd = Get-Command $name -ErrorAction SilentlyContinue
  if ($cmd) { $py = $cmd.Source; break }
}
if (-not $py) { throw "Python 3 not found. Install it from https://www.python.org/downloads/ (tick 'Add python.exe to PATH')." }

$script = Join-Path $app "piggy_input.py"
$shortcut = Join-Path ([Environment]::GetFolderPath("Startup")) "piggy-input.lnk"
$shell = New-Object -ComObject WScript.Shell
$link = $shell.CreateShortcut($shortcut)
$link.TargetPath = $py
$link.Arguments = "-I `"$script`""
$link.WorkingDirectory = $app
$link.WindowStyle = 7
$link.Save()

Start-Process -FilePath $py -ArgumentList "-I", "`"$script`"" -WorkingDirectory $app
Write-Host "piggy-input installed and running. It will start automatically when you log in."
