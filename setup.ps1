# One-shot local setup (Windows / PowerShell): build, start, and seed.
# Usage:  ./setup.ps1
$ErrorActionPreference = "Stop"

Write-Host "==> Building and starting containers..."
docker compose up -d --build

Write-Host "==> Seeding database (retries until the DB is ready)..."
$max = 20
for ($i = 1; $i -le $max; $i++) {
    docker compose exec -T web python seed.py
    if ($LASTEXITCODE -eq 0) {
        Write-Host "`n==> Done. Open http://localhost:5000  (admin@local.test / Password@123)"
        exit 0
    }
    if ($i -eq $max) { Write-Host "Seed failed after $max attempts."; exit 1 }
    Start-Sleep -Seconds 3
}
