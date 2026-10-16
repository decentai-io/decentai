# Builds the white paper: joins parts/*.html into whitepaper.html, then
# prints it to decentai-whitepaper.pdf with headless Microsoft Edge (or
# Chrome), which every Windows machine has.
$ErrorActionPreference = 'Stop'
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

$parts = Get-ChildItem (Join-Path $here 'parts') -Filter '*.html' | Sort-Object Name
$html = ($parts | ForEach-Object { Get-Content $_.FullName -Raw -Encoding UTF8 }) -join "`n"
$html += "`n</body>`n</html>`n"
[IO.File]::WriteAllText((Join-Path $here 'whitepaper.html'), $html, [Text.UTF8Encoding]::new($false))

$browser = @(
  "${env:ProgramFiles(x86)}\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Microsoft\Edge\Application\msedge.exe",
  "$env:ProgramFiles\Google\Chrome\Application\chrome.exe"
) | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $browser) { throw 'Neither Edge nor Chrome was found.' }

# Printed to a path without spaces, then copied: the browser's own
# argument parsing does not take spaces in --print-to-pdf.
$tmp = Join-Path $env:TEMP 'decentai-whitepaper.pdf'
Remove-Item $tmp -ErrorAction SilentlyContinue
$url = 'file:///' + (((Join-Path $here 'whitepaper.html') -replace '\\', '/') -replace ' ', '%20')
Start-Process -FilePath $browser -Wait -WindowStyle Hidden -ArgumentList @(
  '--headless=new', '--disable-gpu', '--no-pdf-header-footer',
  "--user-data-dir=$env:TEMP\decentai-whitepaper-browser",
  "--print-to-pdf=$tmp", $url)
if (-not (Test-Path $tmp)) { throw 'The browser did not write the PDF.' }
Copy-Item $tmp (Join-Path $here 'decentai-whitepaper.pdf') -Force
Write-Output "wrote decentai-whitepaper.pdf"
