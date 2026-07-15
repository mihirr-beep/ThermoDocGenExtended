# PowerShell script to set up Windows Task Scheduler for Equipment Reminder Emails
# Run this script as Administrator

$ErrorActionPreference = "Stop"

Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host "Equipment Reminder Email - Task Scheduler Setup" -ForegroundColor Cyan
Write-Host "=" * 60 -ForegroundColor Cyan
Write-Host ""

# Get the current directory (where this script is located)
$scriptPath = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectPath = $scriptPath

Write-Host "Project Path: $projectPath" -ForegroundColor Yellow

# Find Python executable
$pythonExe = $null

# Check for venv Python first
$venvPython = Join-Path $projectPath "venv\Scripts\python.exe"
if (Test-Path $venvPython) {
    $pythonExe = $venvPython
    Write-Host "Found Python in venv: $pythonExe" -ForegroundColor Green
}
else {
    # Check for venv_new
    $venvNewPython = Join-Path $projectPath "venv_new\Scripts\python.exe"
    if (Test-Path $venvNewPython) {
        $pythonExe = $venvNewPython
        Write-Host "Found Python in venv_new: $pythonExe" -ForegroundColor Green
    }
    else {
        # Try to find Python in PATH
        $pythonExe = (Get-Command python -ErrorAction SilentlyContinue).Source
        if ($pythonExe) {
            Write-Host "Found Python in PATH: $pythonExe" -ForegroundColor Green
        }
        else {
            Write-Host "ERROR: Could not find Python executable!" -ForegroundColor Red
            Write-Host "Please ensure Python is installed or venv is set up." -ForegroundColor Red
            exit 1
        }
    }
}

# Verify the reminder script exists
$reminderScript = Join-Path $projectPath "send_equipment_reminders.py"
if (-not (Test-Path $reminderScript)) {
    Write-Host "ERROR: send_equipment_reminders.py not found!" -ForegroundColor Red
    exit 1
}

Write-Host "Reminder Script: $reminderScript" -ForegroundColor Yellow
Write-Host ""

# Task Scheduler settings
$taskName = "EquipmentReminderEmails"
$taskDescription = "Send daily equipment reminder emails for calibration, IC, and maintenance due dates"

# Check if task already exists
$existingTask = Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue
if ($existingTask) {
    Write-Host "Task '$taskName' already exists." -ForegroundColor Yellow
    $response = Read-Host "Do you want to delete and recreate it? (Y/N)"
    if ($response -eq "Y" -or $response -eq "y") {
        Unregister-ScheduledTask -TaskName $taskName -Confirm:$false
        Write-Host "Deleted existing task." -ForegroundColor Green
    }
    else {
        Write-Host "Keeping existing task. Exiting." -ForegroundColor Yellow
        exit 0
    }
}

Write-Host "Creating scheduled task..." -ForegroundColor Cyan

# Create the action (what to run)
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument "`"$reminderScript`"" -WorkingDirectory $projectPath

# Create the trigger (when to run - daily at 9:00 AM)
$trigger = New-ScheduledTaskTrigger -Daily -At "9:00AM"

# Create settings
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -RunOnlyIfNetworkAvailable

# Create the principal (run as current user)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Highest

# Register the task
try {
    Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Description $taskDescription | Out-Null
    
    Write-Host ""
    Write-Host "=" * 60 -ForegroundColor Green
    Write-Host "SUCCESS: Task scheduled successfully!" -ForegroundColor Green
    Write-Host "=" * 60 -ForegroundColor Green
    Write-Host ""
    Write-Host "Task Name: $taskName" -ForegroundColor Yellow
    Write-Host "Schedule: Daily at 9:00 AM" -ForegroundColor Yellow
    Write-Host "Script: $reminderScript" -ForegroundColor Yellow
    Write-Host ""
    Write-Host "To verify the task:" -ForegroundColor Cyan
    Write-Host "  1. Open Task Scheduler" -ForegroundColor White
    Write-Host "  2. Look for 'EquipmentReminderEmails' in Task Scheduler Library" -ForegroundColor White
    Write-Host ""
    Write-Host "To test the task manually:" -ForegroundColor Cyan
    Write-Host "  Right-click the task -> Run" -ForegroundColor White
    Write-Host ""
    Write-Host "To view/edit the task:" -ForegroundColor Cyan
    Write-Host "  Right-click the task -> Properties" -ForegroundColor White
    Write-Host ""
    
}
catch {
    Write-Host ""
    Write-Host "ERROR: Failed to create scheduled task!" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host ""
    Write-Host "Make sure you're running PowerShell as Administrator." -ForegroundColor Yellow
    Write-Host "Right-click PowerShell -> Run as Administrator" -ForegroundColor Yellow
    exit 1
}
