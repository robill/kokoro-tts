[CmdletBinding()]
param(
    [ValidateSet('cpu', 'cuda')]
    [string]$Provider = 'cuda',
    [switch]$SkipModels,
    [string]$CudaBin = $env:CUDA_BIN_PATH,
    [string]$CudnnBin = $env:CUDNN_BIN_PATH
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $ProjectRoot

$PythonVersion = '3.11.9'
$Environment = Join-Path $ProjectRoot '.venv_kokoro_061'
$EnvironmentPython = Join-Path $Environment 'Scripts\python.exe'
$LockFile = Join-Path $ProjectRoot 'requirements-bilingual-audio.lock.txt'

function Get-Sha256([string]$Path) {
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Assert-Hash([string]$Path, [string]$Expected) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Required asset is missing: $Path"
    }
    $Actual = Get-Sha256 $Path
    if ($Actual -ne $Expected) {
        throw "SHA-256 mismatch for $Path. Expected $Expected; found $Actual."
    }
    Write-Host "Verified SHA-256: $Path"
}

if ($Provider -eq 'cuda' -and $env:CUDA_PATH -and -not $CudaBin) {
    $CudaBin = Join-Path $env:CUDA_PATH 'bin'
}
if ($Provider -eq 'cuda' -and $env:CUDNN_PATH -and -not $CudnnBin) {
    if (Test-Path (Join-Path $env:CUDNN_PATH 'bin')) {
        $CudnnBin = Join-Path $env:CUDNN_PATH 'bin'
    } else {
        $CudnnBin = $env:CUDNN_PATH
    }
}

$PyLauncher = Get-Command py -ErrorAction SilentlyContinue
if (-not $PyLauncher) {
    throw 'The Windows Python launcher (py.exe) is required. Install CPython 3.11.9 x64 first.'
}
$InstalledVersions = & py -0p
if ($LASTEXITCODE -ne 0 -or -not ($InstalledVersions | Select-String -SimpleMatch '3.11')) {
    throw 'CPython 3.11 x64 was not found. Install CPython 3.11.9 x64 and rerun this script.'
}

if (-not (Test-Path -LiteralPath $EnvironmentPython)) {
    Write-Host "Creating isolated environment with CPython $PythonVersion..."
    & py -3.11 -m venv $Environment
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv_kokoro_061.' }
}
$DetectedPython = & $EnvironmentPython --version 2>&1
if ($DetectedPython -notmatch '^Python 3\.11\.9$') {
    throw "$EnvironmentPython uses $DetectedPython. This lock was verified with CPython $PythonVersion x64; recreate the venv with that version."
}

Write-Host 'Installing locked bilingual-audio Python dependencies...'
& $EnvironmentPython -m pip install --upgrade 'pip==26.2.1'
if ($LASTEXITCODE -ne 0) { throw 'Could not install the pinned pip version.' }
& $EnvironmentPython -m pip install --no-deps -r $LockFile
if ($LASTEXITCODE -ne 0) { throw 'Could not install the locked Python packages.' }

# Do not install CPU and GPU ONNX Runtime distributions together; they expose the same `onnxruntime` module.
& $EnvironmentPython -m pip uninstall -y onnxruntime onnxruntime-gpu | Out-Host
if ($Provider -eq 'cuda') {
    Write-Host 'Installing onnxruntime-gpu 1.23.2 (CUDA 12.x / cuDNN 9 compatible build)...'
    & $EnvironmentPython -m pip install --no-deps 'onnxruntime-gpu==1.23.2'
} else {
    Write-Host 'Installing onnxruntime CPU 1.23.2...'
    & $EnvironmentPython -m pip install --no-deps 'onnxruntime==1.23.2'
}
if ($LASTEXITCODE -ne 0) { throw "Could not install the requested ONNX Runtime provider ($Provider)." }

$ModelDirectory = Join-Path $ProjectRoot 'models'
New-Item -ItemType Directory -Force -Path $ModelDirectory | Out-Null
$Assets = @(
    @{
        Name = 'kokoro-v1.1-zh.onnx'
        Url = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.1-zh.onnx'
        Path = Join-Path $ModelDirectory 'kokoro-v1.1-zh.onnx'
        Sha256 = '859f9ded9f53be16c24857cdab3254a45da53c3afd5ba6ef134c7de3f822e326'
    },
    @{
        Name = 'voices-v1.1-zh.bin'
        Url = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.1-zh.bin'
        Path = Join-Path $ModelDirectory 'voices-v1.1-zh.bin'
        Sha256 = '14cb6186c99e4f6016871405f62046c5df863ae27465cbdc4ee08be7dd703acd'
    },
    @{
        Name = 'kokoro-v1.0.onnx'
        Url = 'https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/kokoro-v1.0.onnx'
        Path = Join-Path $ProjectRoot 'kokoro-v1.0.onnx'
        Sha256 = '7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5'
    },
    @{
        Name = 'voices-v1.0.bin'
        Url = 'https://github.com/nazdridoy/kokoro-tts/releases/download/v1.0.0/voices-v1.0.bin'
        Path = Join-Path $ProjectRoot 'voices-v1.0.bin'
        Sha256 = 'd19762d46cf0e6648cb28a7711df1637aad15818185d13f4ff840d57f2f6dfed'
    }
)

if (-not $SkipModels) {
    foreach ($Asset in $Assets) {
        if (-not (Test-Path -LiteralPath $Asset.Path -PathType Leaf)) {
            Write-Host "Downloading $($Asset.Name)..."
            Invoke-WebRequest -Uri $Asset.Url -OutFile $Asset.Path
        }
        Assert-Hash $Asset.Path $Asset.Sha256
    }
} else {
    foreach ($Asset in $Assets) { Assert-Hash $Asset.Path $Asset.Sha256 }
}

if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue) -or -not (Get-Command ffprobe -ErrorAction SilentlyContinue)) {
    throw 'Install FFmpeg and ensure both ffmpeg.exe and ffprobe.exe are on PATH.'
}

if ($Provider -eq 'cuda') {
    if (-not $CudaBin) { $CudaBin = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.9\bin' }
    if (-not $CudnnBin) { $CudnnBin = 'C:\Program Files\NVIDIA\CUDNN\v9.14\bin\12.9' }
    if ((Test-Path -LiteralPath $CudaBin) -and (Test-Path -LiteralPath $CudnnBin)) {
        $env:PATH = "$CudaBin;$CudnnBin;$env:PATH"
        $env:ONNX_PROVIDER = 'CUDAExecutionProvider'
    } else {
        throw "CUDA selected but runtime bin folders were not found. Set CUDA_BIN_PATH and CUDNN_BIN_PATH, or rerun with -Provider cpu. CUDA_BIN_PATH='$CudaBin'; CUDNN_BIN_PATH='$CudnnBin'."
    }
} else {
    Remove-Item Env:ONNX_PROVIDER -ErrorAction SilentlyContinue
}

Write-Host 'Validating installed runtime and available ONNX providers...'
$Validation = @'
import importlib.metadata as metadata
import numpy
import onnxruntime as ort
import soundfile
from bs4 import BeautifulSoup
from kokoro_onnx import Kokoro
from misaki import zh
import os
import numpy as np
provider = os.environ.get("ONNX_PROVIDER", "CPUExecutionProvider")
if provider == "CUDAExecutionProvider" and provider not in ort.get_available_providers():
    raise SystemExit("CUDAExecutionProvider is unavailable in this ONNX Runtime installation.")
zh_model = Kokoro("models/kokoro-v1.1-zh.onnx", "models/voices-v1.1-zh.bin")
en_model = Kokoro("kokoro-v1.0.onnx", "voices-v1.0.bin")
if provider not in zh_model.sess.get_providers() or provider not in en_model.sess.get_providers():
    raise SystemExit(f"Requested provider {provider} did not initialize for both models.")
phonemes, _ = zh.ZHG2P(version="1.1")("千里之行，始于足下。")
zh_audio, zh_rate = zh_model.create(phonemes, voice="zf_003", speed=1.0, is_phonemes=True)
en_audio, en_rate = en_model.create("Environment check.", voice="af_heart", speed=1.0, lang="en-us")
if not np.isfinite(zh_audio).all() or not np.isfinite(en_audio).all() or not len(zh_audio) or not len(en_audio):
    raise SystemExit("Chinese/English model smoke inference returned invalid audio.")
print("Chinese model providers", zh_model.sess.get_providers())
print("English model providers", en_model.sess.get_providers())
print("kokoro-onnx", metadata.version("kokoro-onnx"))
print("misaki-fork", metadata.version("misaki-fork"))
print("onnxruntime", ort.__version__)
print("available providers", ort.get_available_providers())
print("numpy", numpy.__version__)
print("soundfile", soundfile.__version__)
print("G2P", phonemes)
'@
& $EnvironmentPython -c $Validation
if ($LASTEXITCODE -ne 0) { throw 'Runtime validation failed.' }

Write-Host ''
Write-Host 'Environment setup complete.'
Write-Host 'Next: copy .env.example to .env and configure the approved VIO endpoint without committing .env.'
Write-Host 'Check out/prepare the paired bilingual EPUB, then run the generic pipeline batch file.'
