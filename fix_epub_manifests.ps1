param(
    [string]$Pattern = "TGR*.epub"
)

Add-Type -AssemblyName System.IO.Compression.FileSystem

$epubFiles = Get-ChildItem -Filter $Pattern | Where-Object { -not $_.PSIsContainer }

if ($epubFiles.Count -eq 0) {
    Write-Host "No TGR*.epub files found"
    exit
}

Write-Host "Found $($epubFiles.Count) EPUB file(s)`n"

foreach ($epub in $epubFiles) {
    $epubName = $epub.Name
    $epubPath = $epub.FullName
    $tempDir = "epub_temp_$(Get-Random)"
    
    Write-Host "Processing: $epubName"
    
    try {
        Write-Host "  Extracting..."
        if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
        New-Item $tempDir -ItemType Directory | Out-Null
        [System.IO.Compression.ZipFile]::ExtractToDirectory($epubPath, $tempDir)
        
        $opfFile = Get-ChildItem -Path $tempDir -Name "content.opf" -Recurse
        if (-not $opfFile) {
            Write-Host "  Warning: No content.opf found"
            Remove-Item $tempDir -Recurse -Force
            continue
        }
        
        $opfPath = Join-Path $tempDir $opfFile
        [xml]$opfXml = Get-Content $opfPath
        
        # Create namespace manager for XPath queries
        $nsamgr = New-Object System.Xml.XmlNamespaceManager($opfXml.NameTable)
        $nsamgr.AddNamespace("opf", "http://www.idpf.org/2007/opf")
        
        # Get all manifest items and check if their files exist in the archive
        $allItems = $opfXml.SelectNodes("//opf:item", $nsamgr)
        $itemsToRemove = @()

        foreach ($item in $allItems) {
            $href = $item.GetAttribute("href")
            if ($href) {
                $fullPath = Join-Path $tempDir (Join-Path "OEBPS" $href)
                # Also try relative to opf location
                $altPath  = Join-Path $tempDir $href
                if (-not (Test-Path $fullPath) -and -not (Test-Path $altPath)) {
                    Write-Host "    Missing: $href"
                    $itemsToRemove += $item
                }
            }
        }
        
        if ($itemsToRemove.Count -gt 0) {
            Write-Host "  Removing $($itemsToRemove.Count) missing reference(s)..."
            $manifest = $opfXml.SelectSingleNode("//opf:manifest", $nsamgr)
            foreach ($item in $itemsToRemove) {
                $manifest.RemoveChild($item) | Out-Null
            }
            $opfXml.Save($opfPath)
            Write-Host "  Manifest updated"
        } else {
            Write-Host "  No missing images found"
        }
        
        Write-Host "  Repackaging..."
        $backupPath = "$epubPath.backup"
        Copy-Item $epubPath $backupPath
        Remove-Item $epubPath -Force
        
        [System.IO.Compression.ZipFile]::CreateFromDirectory(
            $tempDir, $epubPath, 
            [System.IO.Compression.CompressionLevel]::Optimal, $false
        )
        
        Write-Host "  Done`n"
        Remove-Item $backupPath -Force
    }
    catch {
        Write-Host "  Error: $_"
        $backupPath = "$epubPath.backup"
        if (Test-Path $backupPath) {
            Copy-Item $backupPath $epubPath -Force
            Remove-Item $backupPath
        }
    }
    finally {
        if (Test-Path $tempDir) { Remove-Item $tempDir -Recurse -Force }
    }
}

Write-Host "Complete!"
