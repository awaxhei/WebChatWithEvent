# Build the Android APK.
# The APK embeds CPython (Chaquopy) and the whole Flask backend, so the app
# runs with NO PC and NO external server.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1            # debug
#   powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1 -Release   # release (debug-signed)
#
param(
    [switch]$Release,
    [string]$JdkHome = "D:\tools\jdk17",
    [string]$SdkHome = "D:\tools\android-sdk",
    [string]$GradleHome = "D:\tools\gradle-8.7",
    [string]$PythonExe = "C:\Users\20421\anaconda3\python.exe"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$android = Join-Path $root "android"
$pyDir = Join-Path $android "app\src\main\python"
$assetsDir = Join-Path $android "app\src\main\assets"

if (-not (Test-Path "$JdkHome\bin\java.exe")) { throw "JDK not found: $JdkHome (pass -JdkHome)" }
if (-not (Test-Path "$SdkHome\platforms\android-34\android.jar")) { throw "Android SDK not found: $SdkHome (pass -SdkHome)" }
if (-not (Test-Path "$GradleHome\bin\gradle.bat")) { throw "Gradle not found: $GradleHome (pass -GradleHome)" }
if (-not (Test-Path $PythonExe)) { throw "Python not found: $PythonExe (pass -PythonExe, used only for pip during build)" }

# ---------------------------------------------------------------- 1) backend sources
Write-Host "[1/3] copying python backend into the app ..."
if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $pyDir "core") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $pyDir "routes") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $pyDir "services") | Out-Null

Copy-Item (Join-Path $root "app.py") $pyDir -Force
Copy-Item (Join-Path $root "mobile_boot.py") $pyDir -Force
# config.py ???? Key??? APK?????Key ? App ??????
Copy-Item (Join-Path $root "config_template.py") (Join-Path $pyDir "config.py") -Force
foreach ($sub in @("core", "routes", "services")) {
    Get-ChildItem (Join-Path $root $sub) -Filter *.py -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $pyDir $sub) -Force
    }
}

# ??????? asset ????????? Kotlin ??
New-Item -ItemType Directory -Force -Path $assetsDir | Out-Null
Copy-Item (Join-Path $root "yuxia_prompt.txt") (Join-Path $assetsDir "yuxia_prompt.txt") -Force
Copy-Item (Join-Path $root "yuxia_memes.txt") (Join-Path $assetsDir "yuxia_memes.txt") -Force

# ---------------------------------------------------------------- 2) gradle
Set-Content -Path (Join-Path $android "local.properties") -Value ("sdk.dir=" + $SdkHome.Replace('\', '\\')) -Encoding ASCII
$env:JAVA_HOME = $JdkHome
$env:ANDROID_HOME = $SdkHome
$env:ANDROID_SDK_ROOT = $SdkHome
$env:YUXIA_PYTHON = $PythonExe

$task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
Write-Host "[2/3] gradle $task (first build downloads CPython + pip packages) ..."
Push-Location $android
try {
    & "$GradleHome\bin\gradle.bat" --no-daemon --console=plain $task
    if ($LASTEXITCODE -ne 0) { throw "build failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------- 3) collect apk
$apk = if ($Release) {
    Join-Path $android "app\build\outputs\apk\release\app-release.apk"
} else {
    Join-Path $android "app\build\outputs\apk\debug\app-debug.apk"
}
if (-not (Test-Path $apk)) { throw "artifact not found: $apk" }

$dist = Join-Path $root "static\download"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$target = Join-Path $dist "yuxia.apk"
Copy-Item $apk $target -Force

$size = [math]::Round((Get-Item $target).Length / 1MB, 2)
Write-Host ""
Write-Host "[3/3] APK: $target  ($size MB)"
Write-Host "Install on the phone: copy the file over, or open http://<PC-IP>:5000/app.apk while the web app is running."
