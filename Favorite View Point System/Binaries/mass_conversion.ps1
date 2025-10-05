param (
    [string[]]$Args
)

function Write-LogLine {
    param ($LogPath, $pngFile, $x, $y, $imageCount, $width, $height, $format)
    Add-Content $LogPath "$pngFile x=$x y=$y image_count=$imageCount width=$width height=$height format=$format"
}

function Read-LogFile {
    param ($LogPath)
    $map = @{}
    if (Test-Path $LogPath) {
        Get-Content $LogPath | ForEach-Object {
            if ($_ -match "(\S+)\s+x=(\d+)\s+y=(\d+)\s+image_count=(\d+)\s+width=(\d+)\s+height=(\d+)\s+format=(\d+)") {
                $png = $Matches[1]
                $map[$png] = @{
                    x=$Matches[2]; y=$Matches[3]; image_count=$Matches[4];
                    width=$Matches[5]; height=$Matches[6]; format=$Matches[7]
                }
            }
        }
    }
    return $map
}

# --- Detect drag & drop ---
$InputDir = $null
$LogFile = $null

foreach ($a in $Args) {
    if (Test-Path $a -PathType Container) {
        $InputDir = (Resolve-Path $a).Path
    }
    elseif ($a.ToLower().EndsWith(".txt")) {
        $LogFile = (Resolve-Path $a).Path
    }
}

if (-not $InputDir) {
    Write-Host "============================"
    Write-Host " NVSGTool Mass Converter"
    Write-Host "============================"
    Write-Host "1. Decode NVSG -> PNG"
    Write-Host "2. Encode PNG -> NVSG (no extension)"
    Write-Host "Q. Quit"
    Write-Host "============================"
    $choice = Read-Host "Choose an option"

    switch ($choice) {
        "1" { $mode = "decode" }
        "2" { $mode = "encode" }
        "Q" { exit }
        "q" { exit }
        default { Write-Host "Invalid choice"; exit 1 }
    }

    $InputDir = Read-Host "Enter input folder path"

    if ($mode -eq "encode") {
        $LogFile = (Read-Host "Drag or enter full path to decode_log.txt").Trim('"')
    }

    $OutputDir = Read-Host "Enter output folder path"
}
else {
    if ($LogFile) { $mode = "encode" } else { $mode = "decode" }

    if ($mode -eq "decode") {
        $OutputDir = Join-Path (Split-Path $InputDir -Parent) "converted"
    } else {
        $OutputDir = Join-Path (Split-Path $InputDir -Parent) "reencoded"
    }

    Write-Host "Drag & drop mode detected"
    Write-Host "Mode: $mode"
    Write-Host "Input folder: $InputDir"
    Write-Host "Output folder: $OutputDir"
    if ($LogFile) { Write-Host "Log file: $LogFile" }
}

if (!(Test-Path $OutputDir)) {
    New-Item -ItemType Directory -Path $OutputDir | Out-Null
}

# --- Decode ---
if ($mode -eq "decode") {
    $logPath = Join-Path $OutputDir "decode_log.txt"
    if (Test-Path $logPath) { Remove-Item $logPath }
    Get-ChildItem -Path $InputDir | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
        $base = [System.IO.Path]::GetFileNameWithoutExtension($_.Name)
        $pngPath = (Join-Path $OutputDir "$base.png")
        Write-Host "Decoding $($_.Name)..."
        $output = & java -jar NVSGTool.jar --decode $_.FullName $pngPath
        Write-Host $output
        if ($output -match "x=(\d+), y=(\d+), count=(\d+), width=(\d+), height=(\d+), format=(\d+)") {
            Write-LogLine $logPath "$base.png" $Matches[1] $Matches[2] $Matches[3] $Matches[4] $Matches[5] $Matches[6]
        }
    }
    Write-Host "Saved log: $logPath"
}
# --- Encode ---
elseif ($mode -eq "encode") {
    if (-not $LogFile -or !(Test-Path $LogFile)) {
        Write-Error "No log file provided or not found!"
        exit 1
    }
    $logMap = Read-LogFile $LogFile
    if (-not $logMap.Count) {
        Write-Error "Log file is empty or invalid"
        exit 1
    }

    Get-ChildItem -Path $InputDir -Filter *.png | Where-Object { -not $_.PSIsContainer } | ForEach-Object {
        $base = $_.Name
        if ($logMap.ContainsKey($base)) {
            $vals = $logMap[$base]
            # Guardar SIN extensión
            $outPath = (Join-Path $OutputDir "$([System.IO.Path]::GetFileNameWithoutExtension($base))")
            Write-Host "Encoding $base (x=$($vals.x), y=$($vals.y), count=$($vals.image_count))..."
            java -jar NVSGTool.jar --encode $_.FullName $outPath --x $($vals.x) --y $($vals.y) --image_count $($vals.image_count)
        } else {
            Write-Host "No log entry for $base, skipping"
        }
    }
}
