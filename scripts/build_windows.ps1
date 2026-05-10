$ErrorActionPreference = 'Stop'

python -m pip install --upgrade pip
python -m pip install pyinstaller
pyinstaller --noconfirm --windowed --name indextts_batch_gui src\indextts_batch_gui\__main__.py

if (-not (Test-Path "dist\indextts_batch_gui\indextts_batch_gui.exe")) {
  throw "Build failed: executable not found"
}

Write-Host "Build complete: dist\indextts_batch_gui\indextts_batch_gui.exe"
