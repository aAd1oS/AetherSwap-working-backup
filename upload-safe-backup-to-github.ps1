param(
    [string]$Source = "",
    [string]$Repo = "aAd1oS/AetherSwap-working-backup",
    [switch]$CheckOnly
)

$ErrorActionPreference = "Stop"
$backupRoot = "D:\Vibe Coding\backups"

try {
    if (-not $Source) {
        $latest = Get-ChildItem -LiteralPath $backupRoot -Directory -Filter "AetherSwap-code-*" |
            Where-Object { Test-Path -LiteralPath (Join-Path $_.FullName "source-tree") } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if (-not $latest) {
            throw "No safe AetherSwap backup was found under $backupRoot"
        }
        $Source = Join-Path $latest.FullName "source-tree"
    } elseif (Test-Path -LiteralPath (Join-Path $Source "source-tree")) {
        $Source = Join-Path $Source "source-tree"
    }

    $sourcePath = (Resolve-Path -LiteralPath $Source).Path.TrimEnd("\")
    $backupRootPath = (Resolve-Path -LiteralPath $backupRoot).Path.TrimEnd("\")
    if (-not $sourcePath.StartsWith($backupRootPath + "\", [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to upload a source outside the backup root: $sourcePath"
    }
    if ((Split-Path -Leaf $sourcePath) -ne "source-tree") {
        throw "The upload source must be a filtered source-tree directory: $sourcePath"
    }

    $forbiddenDirNames = @(
        ".git", ".venv", ".playwright", "venv", "log", "__pycache__",
        "playwright_steam", "playwright_buff", "playwright_tmp"
    )
    $forbiddenFileNames = @(
        "credentials.json", "accounts.json", "strategies.json",
        ".env", ".agreed_disclaimer", "debug.log"
    )
    $unsafe = Get-ChildItem -LiteralPath $sourcePath -Recurse -Force | Where-Object {
        if ($_.PSIsContainer) {
            return $forbiddenDirNames -contains $_.Name
        }
        if ($forbiddenFileNames -contains $_.Name) {
            return $true
        }
        return $_.Name -match "^app_config.*" -or
            $_.Name -match "\.maFile$" -or
            $_.Name -match "\.db($|-)" -or
            $_.Name -match "\.log$"
    }
    if ($unsafe) {
        $paths = ($unsafe | Select-Object -First 10 -ExpandProperty FullName) -join [Environment]::NewLine
        throw "Unsafe runtime files were found. Upload stopped:`n$paths"
    }

    Write-Host "Safe source: $sourcePath"
    Write-Host "Repository:  $Repo"
    Write-Host "Files:       $(@(Get-ChildItem -LiteralPath $sourcePath -Recurse -File).Count)"
    $sourceUpdatedAt = (Get-Item -LiteralPath $sourcePath).LastWriteTime
    Write-Host "Snapshot:    $($sourceUpdatedAt.ToString('yyyy-MM-dd HH:mm:ss'))"
    if ($sourceUpdatedAt -lt (Get-Date).AddDays(-1)) {
        Write-Warning "This safe snapshot is older than 24 hours. Create a current safe backup before uploading if you expect recent changes."
    }

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        throw "git was not found in PATH"
    }
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI (gh) was not found in PATH"
    }
    & gh auth status
    if ($LASTEXITCODE -ne 0) {
        throw "GitHub CLI is not logged in. Run: gh auth login"
    }

    if ($CheckOnly) {
        Write-Host "Check-only mode passed. No clone, commit, or push was performed."
        exit 0
    }

    $remoteUrl = "https://github.com/$Repo.git"
    $remoteReady = $false
    for ($attempt = 1; $attempt -le 3; $attempt++) {
        Write-Host "Checking GitHub connection ($attempt/3)..."
        & git ls-remote $remoteUrl HEAD | Out-Null
        if ($LASTEXITCODE -eq 0) {
            $remoteReady = $true
            break
        }
        if ($attempt -lt 3) {
            Write-Warning "GitHub is temporarily unreachable. Retrying in 5 seconds..."
            Start-Sleep -Seconds 5
        }
    }
    if (-not $remoteReady) {
        throw "GitHub remained unreachable after 3 attempts. Keep the network route that can open github.com, then run this script again."
    }

    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $uploadPath = Join-Path $backupRoot "AetherSwap-github-upload-$stamp"
    if (Test-Path -LiteralPath $uploadPath) {
        throw "Upload workspace already exists: $uploadPath"
    }

    & gh repo clone $Repo $uploadPath
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clone $Repo"
    }

    & git -C $uploadPath rm -r --ignore-unmatch .
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to clear tracked files in the temporary upload workspace"
    }

    & robocopy $sourcePath $uploadPath /E /XD .git
    $robocopyCode = $LASTEXITCODE
    if ($robocopyCode -gt 7) {
        throw "robocopy failed with exit code $robocopyCode"
    }

    & git -C $uploadPath add -A
    if ($LASTEXITCODE -ne 0) {
        throw "git add failed"
    }

    Write-Host ""
    Write-Host "Pending Git changes:"
    & git -C $uploadPath status --short
    & git -C $uploadPath diff --cached --quiet
    if ($LASTEXITCODE -eq 0) {
        Write-Host "No changes need to be uploaded."
        exit 0
    }

    Write-Host ""
    $answer = Read-Host "Type YES to commit and push these changes"
    if ($answer -cne "YES") {
        Write-Host "Cancelled. Nothing was committed or pushed."
        exit 0
    }

    $message = "Update AetherSwap working backup $(Get-Date -Format 'yyyy-MM-dd')"
    & git -C $uploadPath commit -m $message
    if ($LASTEXITCODE -ne 0) {
        throw "git commit failed"
    }
    & git -C $uploadPath push origin main
    if ($LASTEXITCODE -ne 0) {
        throw "git push failed"
    }

    Write-Host "Upload completed: https://github.com/$Repo"
    Write-Host "Temporary upload workspace: $uploadPath"
    exit 0
} catch {
    Write-Error $_
    exit 1
}
