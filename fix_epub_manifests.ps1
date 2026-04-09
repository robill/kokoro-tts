Add-Type -AssemblyName System.IO.Compression.FileSystem

param(
    [string]$Pattern = "TGR*.epub"
)

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
        
        # Get all items with Images/ in href
        $allItems = $opfXml.SelectNodes("//opf:item", $nsamgr)
        $itemsToRemove = @()
        
        foreach ($item in $allItems) {
            $imagePath = $item.GetAttribute("href")
            if ($imagePath -and $imagePath -like "*Images/*") {
                $fullImagePath = Join-Path $tempDir $imagePath
                
                if (-not (Test-Path $fullImagePath)) {
                    Write-Host "    Missing: $imagePath"
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
