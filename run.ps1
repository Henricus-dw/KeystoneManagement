# Keystone — one-shot launcher (Windows PowerShell)
if (-not (Test-Path ".venv")) {
    py -3 -m venv .venv
    .\.venv\Scripts\python.exe -m pip install --upgrade pip
}
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Write-Host "Keystone backend running on port 8000" -ForegroundColor Cyan
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
