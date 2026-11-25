package com.yuxia.chat

import android.content.Context
import org.json.JSONObject
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/** 更新日志里的一条版本记录；来源是打包时一起装进 APK 的 CHANGELOG.md */
data class ChangelogSection(
    val version: String,
    val title: String,
    val lines: List<String>,
)

/** 内置后端自报的版本（来自 /api/ping），用来核对它确实是随 APK 装进去的那一份 */
data class BackendBuild(
    val version: String = "",
    val sha: String = "",
    val builtAt: String = "",
    val online: Boolean = false,
)

/**
 * App 自身的版本信息。
 *
 * 版本号只有两个来源：编译期生成的 BuildConfig（来自 build.gradle.kts），
 * 以及打包脚本写进 assets/build_info.json 的构建时间与提交。
 * 安装时间是系统记录的，覆盖安装后会变——用它判断"这次到底装上新的没有"最直接。
 */
data class AppBuild(
    val version: String,
    val versionCode: Int,
    val builtAt: String,
    val sha: String,
    val installedAt: String,
    val sections: List<ChangelogSection>,
) {
    /** 「7.3（53）」这种写法，界面上到处都用它 */
    val label: String get() = "$version（$versionCode）"

    fun section(version: String): ChangelogSection? =
        sections.firstOrNull { it.version == version }
}

object AppInfo {

    fun load(ctx: Context): AppBuild {
        var version = BuildConfig.VERSION_NAME
        var code = BuildConfig.VERSION_CODE
        var sha = ""
        var builtAt = ""
        runCatching {
            val raw = ctx.assets.open("build_info.json").bufferedReader().use { it.readText() }
            val o = JSONObject(raw)
            o.optString("version").takeIf { it.isNotEmpty() }?.let { version = it }
            o.optInt("version_code", 0).takeIf { it > 0 }?.let { code = it }
            sha = o.optString("sha", "")
            builtAt = o.optString("built_at", "")
        }
        return AppBuild(
            version = version,
            versionCode = code,
            builtAt = builtAt,
            sha = sha,
            installedAt = installedAt(ctx),
            sections = changelog(ctx),
        )
    }

    /** 系统记录的安装/覆盖安装时间 */
    private fun installedAt(ctx: Context): String = runCatching {
        val info = ctx.packageManager.getPackageInfo(ctx.packageName, 0)
        SimpleDateFormat("yyyy-MM-dd HH:mm", Locale.CHINA).format(Date(info.lastUpdateTime))
    }.getOrDefault("")

    private fun changelog(ctx: Context): List<ChangelogSection> = runCatching {
        parseChangelog(ctx.assets.open("changelog.md").bufferedReader().use { it.readText() })
    }.getOrDefault(emptyList())

    /** 「## 7.3（2026-11-25）」开头一节，「- 」开头是条目 */
    fun parseChangelog(text: String): List<ChangelogSection> {
        val out = mutableListOf<ChangelogSection>()
        var version: String? = null
        var title = ""
        var lines = mutableListOf<String>()

        fun flush() {
            val v = version ?: return
            out.add(ChangelogSection(v, title, lines.toList()))
        }

        for (raw in text.lineSequence()) {
            val line = raw.trim()
            if (line.startsWith("## ")) {
                flush()
                title = line.removePrefix("## ").trim()
                version = title.substringBefore('（').substringBefore(' ').trim()
                lines = mutableListOf()
            } else if (line.startsWith("- ") && version != null) {
                lines.add(line.removePrefix("- ").trim())
            }
        }
        flush()
        return out
    }
}
