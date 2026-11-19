# Build the Android APK (JDK17 + Android SDK + Gradle are installed on this machine)
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1            # debug
#   powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1 -Release   # release (debug-signed)
#
param(
    [switch]$Release,
    [string]$JdkHome = "D:\tools\jdk17",
    [string]$SdkHome = "D:\tools\android-sdk",
    [string]$GradleHome = "D:\tools\gradle-8.7"
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$android = Join-Path $root "android"

if (-not (Test-Path "$JdkHome\bin\java.exe")) { throw "JDK not found: $JdkHome (pass -JdkHome)" }
if (-not (Test-Path "$SdkHome\platforms\android-34\android.jar")) { throw "Android SDK not found: $SdkHome (pass -SdkHome)" }
if (-not (Test-Path "$GradleHome\bin\gradle.bat")) { throw "Gradle not found: $GradleHome (pass -GradleHome)" }

# tell Gradle where the SDK is
$sdkEscaped = $SdkHome.Replace('\', '\\')
Set-Content -Path (Join-Path $android "local.properties") -Value "sdk.dir=$sdkEscaped" -Encoding ASCII

$env:JAVA_HOME = $JdkHome
$env:ANDROID_HOME = $SdkHome
$env:ANDROID_SDK_ROOT = $SdkHome

$task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
Push-Location $android
try {
    & "$GradleHome\bin\gradle.bat" --no-daemon --console=plain $task
    if ($LASTEXITCODE -ne 0) { throw "build failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

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
Write-Host "APK: $target  ($size MB)"
Write-Host "On the phone (same Wi-Fi): open http://<PC-LAN-IP>:5000/static/download/yuxia.apk"
