# new_video.ps1 - "New Sukoon Video": pick the Audacity project, paste the script (if any), and the episode
# folder D:\MukhtalifGPT\Episodes\<name>\ is set up for you. Then make_video runs on it (see New Sukoon Video.bat).
# Nothing is moved or deleted: your files are COPIED into the episode folder.
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName Microsoft.VisualBasic
[System.Windows.Forms.Application]::EnableVisualStyles()

$Episodes = $env:EPISODES; if (-not $Episodes) { $Episodes = 'D:\MukhtalifGPT\Episodes' }
$outFile  = Join-Path $env:TEMP 'mukhtalif_new_episode.txt'
$state    = Join-Path $PSScriptRoot 'last_folders.json'
if (Test-Path $outFile) { Remove-Item $outFile -Force }
$imgDir = $env:SUKOON_IMAGES; if (-not $imgDir) { $imgDir = 'D:\Audio asset\Images' }
$last = @{ aup = [Environment]::GetFolderPath('MyDocuments'); img = $imgDir }
if (Test-Path $state) { try { $j = Get-Content $state -Raw | ConvertFrom-Json; if ($j.aup) { $last.aup = $j.aup }; if ($j.img) { $last.img = $j.img } } catch {} }

# an invisible top-most owner so every dialog appears in front (not behind the console)
$owner = New-Object System.Windows.Forms.Form
$owner.TopMost = $true; $owner.ShowInTaskbar = $false

function Ask($text, $title = 'HubGPT Video', $buttons = 'YesNo') {
    return [System.Windows.Forms.MessageBox]::Show($owner, $text, $title, $buttons, 'Question')
}
function PickFile($title, $filter, $dir) {
    $d = New-Object System.Windows.Forms.OpenFileDialog
    $d.Title = $title; $d.Filter = $filter
    if ($dir -and (Test-Path $dir)) { $d.InitialDirectory = $dir }
    if ($d.ShowDialog($owner) -eq 'OK') { return $d.FileName }
    return $null
}
function Say($t) { Write-Host "  $t" }

Write-Host ''
Write-Host '  NEW SUKOON VIDEO'
Write-Host '  ----------------'

# Only TWO questions: which Audacity project, and do you have the script. Everything else is automatic.
# 1) the Audacity project -------------------------------------------------------------------------------
$aup = PickFile 'Choose the Audacity project (.aup3)' 'Audacity project (*.aup3)|*.aup3' $last.aup
if (-not $aup) { Say 'Nothing chosen.'; exit 1 }
$aupDir = Split-Path $aup -Parent
$stem   = [IO.Path]::GetFileNameWithoutExtension($aup)
Say "project : $aup"
function IsOpen($f) {   # Audacity keeps the project file open while it is loaded
    try { $fs = [IO.File]::Open($f, 'Open', 'Read', 'None'); $fs.Close(); return $false } catch { return $true }
}
while ((IsOpen $aup) -or ((Test-Path ($aup + '-wal')) -and ((Get-Item ($aup + '-wal')).Length -gt 0))) {
    $r = Ask "This project is OPEN in Audacity.`n`nPlease SAVE it and CLOSE Audacity, then press OK.`n(Copying it while it is open gives a broken copy.)`n`nCancel = stop" 'Audacity is open' 'OKCancel'
    if ($r -ne 'OK') { exit 1 }
}

# 2) the soundtrack (automatic): your exported mp3/wav with the project's name, else a mix of the project ----
$audio = 'MIX'
$same = @(Get-ChildItem -LiteralPath $aupDir -File | Where-Object { $_.Extension -match '^\.(mp3|wav|m4a|flac)$' -and $_.BaseName -eq $stem }) | Select-Object -First 1
if ($same) { $audio = $same.FullName }
Say ("audio   : " + $(if ($audio -eq 'MIX') { '(no export found - will be mixed from the project)' } else { Split-Path $audio -Leaf }))

# 3) episode name (automatic) = the project's name, e.g. "Sukoon 9.0 - Trip To Bachpan.aup3" ----------------
$name = (($stem -replace '[_]+', ' ') -replace '[\\/:*?"<>|]', '').Trim()
$epRoot = [IO.Path]::GetFullPath($Episodes).TrimEnd('\')
if ([IO.Path]::GetFullPath((Split-Path $aupDir -Parent)).TrimEnd('\') -eq $epRoot) { $name = Split-Path $aupDir -Leaf }  # already in an episode folder
Say "episode : $name"

# 4) script -----------------------------------------------------------------------------------------------
$scriptText = $null
$txt = @("$aupDir\$stem.txt", "$aupDir\script.txt") | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
$r = Ask "Do you have the SCRIPT you read for this episode?`n`nYes = paste it in the next window (2-3 rough versions are fine too)`n         (your exact words + perfect spelling, and much faster)`nNo  = the words are recognised from your voice" 'Script'
$ep = Join-Path $Episodes $name
$preExisted = Test-Path -LiteralPath $ep
if ($r -eq 'Yes') {
    # Notepad: pastes any length, and you can paste 2-3 versions one after another
    New-Item -ItemType Directory -Force -Path $ep | Out-Null
    $sp = Join-Path $ep 'script.txt'
    if (-not (Test-Path -LiteralPath $sp)) {
        $start = if ($txt) { [IO.File]::ReadAllText($txt) } else { '' }
        [IO.File]::WriteAllText($sp, $start, (New-Object System.Text.UTF8Encoding($false)))
    }
    $before = (Get-Item -LiteralPath $sp).LastWriteTime
    :again while ($true) {
    Start-Process notepad.exe -ArgumentList ('"' + $sp + '"')
    Write-Host ''
    Say "Notepad is open with script.txt  (it already shows any script that was there before)."
    Say "Paste your script (several versions: put a line ----- between them), SAVE with Ctrl+S, close Notepad."
    Say "Waiting for you to close Notepad ..."
    $seen = $false; $r2 = 'OK'
    for ($i = 0; $i -lt 7200; $i++) {                  # up to 2 hours
        Start-Sleep -Milliseconds 700
        $open = @(Get-Process notepad -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowTitle -match 'script' })
        if ($open.Count -gt 0) { $seen = $true }
        elseif ($seen) { break }                       # you closed it
        elseif ($i -gt 25) {                           # could not watch the Notepad window: ask instead
            $r2 = [System.Windows.Forms.MessageBox]::Show("When the script is pasted and SAVED in Notepad, press OK.`n`nCancel = continue without a script", 'Paste your script', 'OKCancel', 'Information')
            break
        }
    }
    if ($r2 -eq 'OK' -and (Get-Item -LiteralPath $sp).LastWriteTime -eq $before) {
        $r3 = [System.Windows.Forms.MessageBox]::Show("script.txt was NOT saved - nothing you pasted reached it.`n(New Notepad closes without asking and keeps unsaved text to itself.)`n`nYes = open Notepad again: paste, press Ctrl+S, then close`nNo  = continue with the script that is already there", 'Script not saved', 'YesNo', 'Warning')
        if ($r3 -eq 'Yes') { continue again }
    }
    break
    }
    $content = [IO.File]::ReadAllText($sp)
    if ($r2 -eq 'OK' -and $content.Trim()) {
        Say ("script  : saved ($([math]::Round($content.Length / 1000)) thousand characters)")
    } else {
        if (-not $content.Trim()) { Remove-Item -LiteralPath $sp -ErrorAction SilentlyContinue }
        Say 'No script - the words will be recognised from your voice.'
    }
}

# 4b) title on the opening screen (in Hindi). Empty = the AI suggests 3 in the window and you choose ---------
$titleText = $null
$tf = New-Object System.Windows.Forms.Form
$tf.Text = 'Episode title'; $tf.Size = New-Object System.Drawing.Size(720, 230); $tf.StartPosition = 'CenterScreen'
$tf.TopMost = $true; $tf.FormBorderStyle = 'FixedDialog'; $tf.MaximizeBox = $false; $tf.MinimizeBox = $false
$lb = New-Object System.Windows.Forms.Label
$lb.Text = "Title shown on the opening screen (Hindi, 2-5 words).`nLeave it empty and the AI will suggest 3 titles for you to choose from."
$lb.Location = New-Object System.Drawing.Point(16, 12); $lb.Size = New-Object System.Drawing.Size(680, 44)
$lb.Font = New-Object System.Drawing.Font('Segoe UI', 10)
$tt = New-Object System.Windows.Forms.TextBox
$tt.Location = New-Object System.Drawing.Point(16, 62); $tt.Size = New-Object System.Drawing.Size(670, 40)
$tt.Font = New-Object System.Drawing.Font('Nirmala UI', 16)
$ok = New-Object System.Windows.Forms.Button
$ok.Text = 'OK'; $ok.Location = New-Object System.Drawing.Point(586, 120); $ok.Size = New-Object System.Drawing.Size(100, 38)
$ok.DialogResult = 'OK'; $tf.AcceptButton = $ok
$tf.Controls.Add($lb); $tf.Controls.Add($tt); $tf.Controls.Add($ok)
$tf.Add_Shown({ $tt.Focus() })
if ($tf.ShowDialog($owner) -eq 'OK' -and $tt.Text.Trim()) { $titleText = $tt.Text.Trim() }
Say ("title   : " + $(if ($titleText) { '(your title)' } else { '(AI will suggest - you choose in the window)' }))

# 5) artwork (automatic): an image next to the project, else one named like it in the images folder ---------
function Norm($t) { return ($t.ToLower() -replace '[^a-z0-9]', '') }
$key = Norm $stem
$img = $null; $picked = $false
$here = @(Get-ChildItem -LiteralPath $aupDir -File | Where-Object { $_.Extension -match '^\.(png|jpg|jpeg|webp)$' })
$img = ($here | Where-Object { $_.BaseName -eq $stem } | Select-Object -First 1)
if (-not $img -and (Test-Path -LiteralPath $last.img)) {
    $img = Get-ChildItem -LiteralPath $last.img -File | Where-Object { $_.Extension -match '^\.(png|jpg|jpeg|webp)$' } |
        Where-Object { $k = Norm $_.BaseName; $k -and ($k.Contains($key) -or $key.Contains($k)) } | Select-Object -First 1
}
if ($img) { $img = $img.FullName }
else {
    $img = PickFile "No artwork found for '$stem' - choose the image for this episode" 'Images (*.png;*.jpg;*.jpeg;*.webp)|*.png;*.jpg;*.jpeg;*.webp' $last.img
    if (-not $img) { Say 'No artwork chosen - stopping.'; exit 1 }
    $picked = $true
}
Say "artwork : $(Split-Path $img -Leaf)"

# set up the episode folder (copies - your originals stay where they are) -------------------------------
$inPlace = ([IO.Path]::GetFullPath($aupDir).TrimEnd('\') -eq [IO.Path]::GetFullPath($ep).TrimEnd('\'))
if ($preExisted -and -not $inPlace) {
    $r = Ask "A folder '$name' already exists.`n`nYes = use it (finished steps are reused, files updated)`nNo  = stop" 'Episode exists'
    if ($r -ne 'Yes') { exit 1 }
}
New-Item -ItemType Directory -Force -Path $ep | Out-Null
function CopyIn($src) {
    $dst = Join-Path $ep (Split-Path $src -Leaf)
    if (Test-Path -LiteralPath $dst) {
        $d0 = Get-Item -LiteralPath $dst; $s0 = Get-Item -LiteralPath $src
        if ($d0.Length -eq $s0.Length -and $d0.LastWriteTime -eq $s0.LastWriteTime) { return }   # same file already there
    }
    Say "copying $(Split-Path $src -Leaf) ..."
    Copy-Item -LiteralPath $src -Destination $dst -Force
}
if (-not $inPlace) {
    CopyIn $aup
    if ($audio -ne 'MIX') { CopyIn $audio }
    if ($img) { CopyIn $img }
}
if ($audio -eq 'MIX') {   # a stale exported file in the folder would be picked instead of the mix
    Get-ChildItem -LiteralPath $ep -File | Where-Object { $_.Extension -match '^\.(mp3|m4a|flac)$' } | ForEach-Object { Say "note: $($_.Name) in the folder will be used as the soundtrack" }
}
if ($titleText) {
    [IO.File]::WriteAllText((Join-Path $ep 'title.txt'), $titleText, (New-Object System.Text.UTF8Encoding($false)))
}
if ($scriptText) {
    [IO.File]::WriteAllText((Join-Path $ep 'script.txt'), $scriptText, (New-Object System.Text.UTF8Encoding($false)))
    Say 'script  : saved as script.txt'
}
try { @{ aup = $aupDir; img = $(if ($picked) { Split-Path $img -Parent } else { $last.img }) } | ConvertTo-Json | Set-Content $state } catch {}
Say "episode : $ep"
[IO.File]::WriteAllText($outFile, $ep, (New-Object System.Text.UTF8Encoding($false)))
exit 0
