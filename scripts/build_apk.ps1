# 打包安卓 APK。
# APK 内嵌 CPython（Chaquopy）与整套 Flask 后端，装到手机后不需要电脑、也不需要外部服务器。
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1            # debug
#   powershell -ExecutionPolicy Bypass -File scripts\build_apk.ps1 -Release   # release（debug 签名）
#
# 版本号只认 android\app\build.gradle.kts 里的 versionName / versionCode，
# 脚本把它们连同构建时间、构建提交一起写进 APK，App 内「关于与更新日志」据此显示，
# 装完之后能直接确认手上是哪一个版本。
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
$gradleFile = Join-Path $android "app\build.gradle.kts"

if (-not (Test-Path "$JdkHome\bin\java.exe")) { throw "JDK not found: $JdkHome (pass -JdkHome)" }
if (-not (Test-Path "$SdkHome\platforms\android-34\android.jar")) { throw "Android SDK not found: $SdkHome" }
if (-not (Test-Path "$GradleHome\bin\gradle.bat")) { throw "Gradle not found: $GradleHome (pass -GradleHome)" }
if (-not (Test-Path $PythonExe)) { throw "Python not found: $PythonExe (pass -PythonExe, used only for pip during build)" }

# ---------------------------------------------------------------- 0) 版本与构建标识
if (-not (Test-Path -LiteralPath $gradleFile)) { throw "gradle config not found: $gradleFile" }
$gradle = Get-Content -LiteralPath $gradleFile -Raw
$mName = [regex]::Match($gradle, 'versionName\s*=\s*"([^"]+)"')
$mCode = [regex]::Match($gradle, 'versionCode\s*=\s*(\d+)')
if (-not $mName.Success -or -not $mCode.Success) {
    throw "cannot parse versionName / versionCode from $gradleFile"
}
$verName = $mName.Groups[1].Value
$verCode = [int]$mCode.Groups[1].Value
$stamp = [DateTime]::Now.ToString("yyyy-MM-dd HH:mm")

# 构建提交：优先读 .git/refs/heads/main（git 不一定在 PATH 里，之前就栽在这上面），
# 有 git 可执行文件时再补一个工作区是否干净的标记。
$sha = "unknown"
$shaFile = Join-Path $root ".git\refs\heads\main"
if (Test-Path -LiteralPath $shaFile) {
    $full = (Get-Content -LiteralPath $shaFile -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($full -and $full.Trim().Length -ge 7) { $sha = $full.Trim().Substring(0, 7) }
}
$gitExe = $null
$gitCmd = Get-Command git -ErrorAction SilentlyContinue
if ($gitCmd -and $gitCmd.Source) {
    $gitExe = $gitCmd.Source
} else {
    foreach ($cand in @("$env:ProgramFiles\Git\cmd\git.exe",
                        "$env:LOCALAPPDATA\Programs\Git\cmd\git.exe",
                        "D:\编程\Git\cmd\git.exe")) {
        if (Test-Path -LiteralPath $cand) { $gitExe = $cand; break }
    }
}
if ($gitExe -and $sha -eq "unknown") {
    $gitSha = @(& $gitExe -C $root rev-parse --short HEAD 2>$null)
    if ($gitSha.Count -gt 0 -and $gitSha[0]) { $sha = ([string]$gitSha[0]).Trim() }
}
if ($gitExe) {
    $dirty = @(& $gitExe -C $root status --porcelain 2>$null) | Where-Object { $_ }
    if ($dirty) { $sha = "$sha-dirty" }
}

Write-Host "[0/5] version $verName ($verCode)  commit $sha  built $stamp"

# ---------------------------------------------------------------- 1) 后端源码
Write-Host "[1/5] copying python backend into the app ..."
if (Test-Path $pyDir) { Remove-Item $pyDir -Recurse -Force }
New-Item -ItemType Directory -Force -Path $pyDir | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $pyDir "core") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $pyDir "routes") | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $pyDir "services") | Out-Null

Copy-Item (Join-Path $root "app.py") $pyDir -Force
Copy-Item (Join-Path $root "mobile_boot.py") $pyDir -Force
# config.py 不带 Key；APK 里只放模板，Key 由 App 自己保存
Copy-Item (Join-Path $root "config_template.py") (Join-Path $pyDir "config.py") -Force
foreach ($sub in @("core", "routes", "services")) {
    Get-ChildItem (Join-Path $root $sub) -Filter *.py -File | ForEach-Object {
        Copy-Item $_.FullName (Join-Path $pyDir $sub) -Force
    }
}

# 打包信息：手机端 /api/ping 会带出来，用来确认装上的到底是哪一版
$pyInfo = @(
    "BUILD_VERSION = `"$verName`"",
    "BUILD_VERSION_CODE = $verCode",
    "BUILD_SHA = `"$sha`"",
    "BUILD_TAG = `"$verName ($verCode) $sha`"",
    "BUILD_TIME = `"$stamp`"",
    ""
) -join "`n"
[System.IO.File]::WriteAllText((Join-Path $pyDir "build_info.py"), $pyInfo,
    (New-Object System.Text.UTF8Encoding($false)))

# 同一份信息给 Kotlin 用（Python 还没起来时「关于」页也能显示版本）
New-Item -ItemType Directory -Force -Path $assetsDir | Out-Null
$assetsInfo = [ordered]@{
    version      = $verName
    version_code = $verCode
    sha          = $sha
    built_at     = $stamp
}
[System.IO.File]::WriteAllText((Join-Path $assetsDir "build_info.json"),
    ($assetsInfo | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))
Copy-Item (Join-Path $root "CHANGELOG.md") (Join-Path $assetsDir "changelog.md") -Force

# 人设与梗库同时作为 asset 带上（落盘后由后端读取）
Copy-Item (Join-Path $root "yuxia_prompt.txt") (Join-Path $assetsDir "yuxia_prompt.txt") -Force
Copy-Item (Join-Path $root "yuxia_memes.txt") (Join-Path $assetsDir "yuxia_memes.txt") -Force

# ---------------------------------------------------------------- 2) gradle
Set-Content -Path (Join-Path $android "local.properties") -Value ("sdk.dir=" + $SdkHome.Replace('\', '\\')) -Encoding ASCII
$env:JAVA_HOME = $JdkHome
$env:ANDROID_HOME = $SdkHome
$env:ANDROID_SDK_ROOT = $SdkHome
$env:YUXIA_PYTHON = $PythonExe

$task = if ($Release) { "assembleRelease" } else { "assembleDebug" }
Write-Host "[2/5] gradle $task (first build downloads CPython + pip packages) ..."
Push-Location $android
try {
    & "$GradleHome\bin\gradle.bat" --no-daemon --console=plain $task
    if ($LASTEXITCODE -ne 0) { throw "build failed (exit $LASTEXITCODE)" }
} finally {
    Pop-Location
}

# ---------------------------------------------------------------- 3) 产物路径
$apk = if ($Release) {
    Join-Path $android "app\build\outputs\apk\release\app-release.apk"
} else {
    Join-Path $android "app\build\outputs\apk\debug\app-debug.apk"
}
if (-not (Test-Path $apk)) { throw "artifact not found: $apk" }

# ---------------------------------------------------------------- 4) 校验包内版本
# 读回 APK 里的 assets/build_info.json：确认打进去的就是这一个版本，
# 免得出现「以为换了版本，其实装进去的还是旧的」。
Add-Type -AssemblyName System.IO.Compression.FileSystem
$zip = [System.IO.Compression.ZipFile]::OpenRead($apk)
try {
    $entry = $zip.Entries | Where-Object { $_.FullName -eq "assets/build_info.json" } | Select-Object -First 1
    if (-not $entry) { throw "assets/build_info.json missing from the APK" }
    $reader = New-Object System.IO.StreamReader($entry.Open(), [System.Text.Encoding]::UTF8)
    $packedJson = $reader.ReadToEnd()
    $reader.Close()
} finally {
    $zip.Dispose()
}
$packed = $packedJson | ConvertFrom-Json
if ($packed.version -ne $verName -or [int]$packed.version_code -ne $verCode) {
    throw "packaged version mismatch: built $verName ($verCode), inside $($packed.version) ($($packed.version_code))"
}

# ---------------------------------------------------------------- 5) 发布到下载目录
$dist = Join-Path $root "static\download"
New-Item -ItemType Directory -Force -Path $dist | Out-Null
$target = Join-Path $dist "yuxia.apk"
$named = Join-Path $dist ("yuxia-" + $verName + ".apk")
Copy-Item $apk $target -Force
Copy-Item $apk $named -Force

$size = [math]::Round((Get-Item $target).Length / 1MB, 2)
$apkInfo = [ordered]@{
    version      = $verName
    version_code = $verCode
    sha          = $sha
    built_at     = $stamp
    size_mb      = $size
    file         = ("yuxia-" + $verName + ".apk")
}
[System.IO.File]::WriteAllText((Join-Path $dist "apk_info.json"),
    ($apkInfo | ConvertTo-Json -Compress), (New-Object System.Text.UTF8Encoding($false)))

Write-Host ""
Write-Host "[3/5] APK: $target  ($size MB)"
Write-Host "      version $verName ($verCode)  commit $sha  built $stamp  (verified inside the package)"
Write-Host "      also copied to: $named"
Write-Host "[4/5] metadata: static\download\apk_info.json"
Write-Host "[5/5] Install on the phone: open http://<PC-IP>:5000/app.apk while the web app is running."
