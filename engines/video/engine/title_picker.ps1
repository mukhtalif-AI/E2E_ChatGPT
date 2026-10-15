# title_picker.ps1 - a small window to choose the episode title (Hindi typing / pasting works here,
# unlike the black command window). Called by sleep_pipeline.py:
#   powershell -STA -File title_picker.ps1 <in.json> <out.txt> <seconds>
# in.json = {"episode": "...", "titles": ["...", "...", "..."]}   out.txt = the chosen title (UTF-8)
param([string]$InFile, [string]$OutFile, [int]$Seconds = 180)
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[System.Windows.Forms.Application]::EnableVisualStyles()

$j = [IO.File]::ReadAllText($InFile, [Text.Encoding]::UTF8) | ConvertFrom-Json
$titles = @($j.titles)
$script:choice = $null
$script:left = $Seconds

$f = New-Object System.Windows.Forms.Form
$f.Text = "Title for: " + $j.episode
$f.Size = New-Object System.Drawing.Size(640, (250 + 58 * $titles.Count))
$f.StartPosition = 'CenterScreen'; $f.TopMost = $true; $f.FormBorderStyle = 'FixedDialog'
$f.MaximizeBox = $false; $f.MinimizeBox = $false

$lb = New-Object System.Windows.Forms.Label
$lb.Text = "Choose the title for the opening screen - click one, or type / paste your own below."
$lb.Location = New-Object System.Drawing.Point(16, 12); $lb.Size = New-Object System.Drawing.Size(600, 24)
$lb.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$f.Controls.Add($lb)

$y = 44
foreach ($t in $titles) {
    $b = New-Object System.Windows.Forms.Button
    $b.Text = $t; $b.Tag = $t
    $b.Location = New-Object System.Drawing.Point(16, $y); $b.Size = New-Object System.Drawing.Size(592, 48)
    $b.Font = New-Object System.Drawing.Font('Nirmala UI', 15)
    $b.Add_Click({ param($s, $e) $script:choice = $s.Tag; $f.Close() })
    $f.Controls.Add($b)
    $y += 58
}

$lb2 = New-Object System.Windows.Forms.Label
$lb2.Text = "Your own title:"; $lb2.Location = New-Object System.Drawing.Point(16, ($y + 6))
$lb2.Size = New-Object System.Drawing.Size(200, 22); $lb2.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$f.Controls.Add($lb2)
$tb = New-Object System.Windows.Forms.TextBox
$tb.Location = New-Object System.Drawing.Point(16, ($y + 30)); $tb.Size = New-Object System.Drawing.Size(470, 40)
$tb.Font = New-Object System.Drawing.Font('Nirmala UI', 15)
$f.Controls.Add($tb)
$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'Use this'; $ok.Location = New-Object System.Drawing.Point(496, ($y + 29)); $ok.Size = New-Object System.Drawing.Size(112, 38)
$ok.Add_Click({ if ($tb.Text.Trim()) { $script:choice = $tb.Text.Trim(); $f.Close() } })
$f.Controls.Add($ok)
$f.AcceptButton = $ok

$cd = New-Object System.Windows.Forms.Label
$cd.Location = New-Object System.Drawing.Point(16, ($y + 80)); $cd.Size = New-Object System.Drawing.Size(600, 22)
$cd.Font = New-Object System.Drawing.Font('Segoe UI', 9); $cd.ForeColor = [System.Drawing.Color]::Gray
$f.Controls.Add($cd)

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 1000
$timer.Add_Tick({
    $script:left--
    if ($tb.Text.Length -gt 0 -and $script:left -lt 60) { $script:left = 60 }   # you are typing: wait for you
    $cd.Text = "No choice in $($script:left) s -> the first suggestion is used (you can change it later in title.txt)."
    if ($script:left -le 0) { $f.Close() }
})
$cd.Text = "No choice in $Seconds s -> the first suggestion is used (you can change it later in title.txt)."
$f.Add_Shown({ $f.Activate(); [System.Media.SystemSounds]::Asterisk.Play(); $timer.Start() })
[void]$f.ShowDialog()
$timer.Stop()

if ($script:choice) {
    [IO.File]::WriteAllText($OutFile, $script:choice, (New-Object System.Text.UTF8Encoding($false)))
}
exit 0
