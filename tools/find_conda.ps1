# Prints the full path of conda.bat (Miniconda, Anaconda, Miniforge) when found.
# find_conda.bat runs this only when the usual folders did not have it
# (for example Miniconda installed on D: or in a custom folder).
$ErrorActionPreference = 'SilentlyContinue'
$folders = New-Object System.Collections.Generic.List[string]

# Installed programs list: InstallLocation, or the folder of the uninstaller.
$keys = @(
  'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
  'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
)
foreach ($item in (Get-ItemProperty -Path $keys)) {
  if ("$($item.DisplayName)" -match 'conda|forge') {
    if ($item.InstallLocation) { $folders.Add("$($item.InstallLocation)".Trim('"')) }
    if ($item.UninstallString) { $folders.Add((Split-Path -Parent "$($item.UninstallString)".Trim('"'))) }
  }
}

# Top-level conda folders on every local drive (D:\miniconda3, E:\Miniforge3 ...).
$names = @('miniconda3', 'anaconda3', 'miniforge3', 'mambaforge', 'Miniconda', 'Anaconda', 'conda')
foreach ($drive in [System.IO.DriveInfo]::GetDrives()) {
  if ($drive.DriveType -eq 'Fixed' -and $drive.IsReady) {
    foreach ($n in $names) { $folders.Add((Join-Path $drive.RootDirectory.FullName $n)) }
  }
}

foreach ($f in $folders) {
  if (-not $f) { continue }
  $bat = Join-Path $f 'condabin\conda.bat'
  if (Test-Path -LiteralPath $bat) { Write-Output $bat; exit 0 }
}
exit 1
