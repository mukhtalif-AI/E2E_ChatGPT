$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$stamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$extensions = @('.py', '.html', '.js', '.mjs', '.ts', '.tsx', '.jsx', '.css', '.json', '.toml',
    '.yaml', '.yml', '.xml', '.sql', '.sh', '.cmd', '.ini', '.cfg', '.bat', '.ps1', '.md', '.txt')
$skipDirs = @(
    '.git', '.cache', '__pycache__', 'node_modules', 'libs', 'venv', '.venv',
    '_old', '_tmp', 'logs', 'Code'
)

function Get-SourceFiles([string]$scope, [string]$outputPath) {
    $base = [IO.Path]::GetFullPath($scope).TrimEnd('\')
    $pending = [Collections.Generic.Stack[string]]::new()
    $pending.Push($base)
    $found = [Collections.Generic.List[IO.FileInfo]]::new()
    $outputFull = [IO.Path]::GetFullPath($outputPath)
    while ($pending.Count -gt 0) {
        $directory = $pending.Pop()
        foreach ($entry in Get-ChildItem -LiteralPath $directory -Force) {
            if ($entry.PSIsContainer) {
                if (($skipDirs -contains $entry.Name) -or $entry.Name.StartsWith('_backup', [StringComparison]::OrdinalIgnoreCase) -or
                    ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint)) { continue }
                $pending.Push($entry.FullName)
                continue
            }
            if (($extensions -contains $entry.Extension.ToLowerInvariant()) -and
                ([IO.Path]::GetFullPath($entry.FullName) -ne $outputFull) -and
                ($entry.Name -ine 'ALL_CODE.txt') -and ($entry.Name -ine 'secrets.json') -and
                ($entry.Name -ine 'python.txt')) {
                $found.Add($entry)
            }
        }
    }
    return @($found | Sort-Object FullName)
}

function Write-Snapshot([string]$scope, [string]$outputPath, [string]$title) {
    $scopeFull = [IO.Path]::GetFullPath($scope).TrimEnd('\')
    $files = @(Get-SourceFiles $scope $outputPath)
    $tempPath = "$outputPath.part"
    $encoding = [Text.UTF8Encoding]::new($false)
    $writer = [IO.StreamWriter]::new($tempPath, $false, $encoding)
    try {
        $writer.WriteLine("$title - generated $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss') - $($files.Count) files")
        $writer.WriteLine(('=' * 100))
        foreach ($file in $files) {
            $rel = [IO.Path]::GetFullPath($file.FullName).Substring($scopeFull.Length).TrimStart('\')
            $writer.WriteLine()
            $writer.WriteLine(('=' * 100))
            $writer.WriteLine("# $rel")
            $writer.WriteLine(('=' * 100))
            $writer.WriteLine([IO.File]::ReadAllText($file.FullName))
        }
    }
    finally {
        $writer.Dispose()
    }

    if (Test-Path -LiteralPath $outputPath) {
        Copy-Item -LiteralPath $outputPath -Destination "$outputPath.bak_$stamp" -Force
    }
    Move-Item -LiteralPath $tempPath -Destination $outputPath -Force
    Write-Host "Updated $outputPath ($($files.Count) files)"
}

$mainSnapshot = Join-Path $root 'Code\ALL_CODE.txt'
$videoSnapshot = Join-Path $root 'engines\video\ALL_CODE.txt'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $mainSnapshot) | Out-Null
Write-Snapshot $root $mainSnapshot 'HUBGPT - all source code'
Write-Snapshot (Join-Path $root 'engines\video') $videoSnapshot 'HUBGPT VIDEO ENGINE - all source code'
