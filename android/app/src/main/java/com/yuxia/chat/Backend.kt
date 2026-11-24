package com.yuxia.chat

import android.content.Context
import com.chaquo.python.Python
import com.chaquo.python.android.AndroidPlatform
import java.io.File

/**
 * 内嵌后端：Python（同一套 Flask 逻辑）直接跑在手机里，不存在"电脑上的服务器"。
 *
 * App 启动时把装在 assets 里的人设提示词交给 Python，Python 在应用私有目录
 * 建好 SQLite、表情包目录，然后只在 127.0.0.1 上起 Flask，
 * 并返回一个本机 token（单机模式不需要登录）。
 */
object Backend {
    const val PORT = 54123

    @Volatile
    private var started = false

    fun baseUrl(): String = "http://127.0.0.1:$PORT"

    private fun promptText(ctx: Context): String = assetText(ctx, "yuxia_prompt.txt")

    /** 梗库（yuxia_memes.txt）：同样由 assets 带过去，落盘后由后端读取 */
    private fun memesText(ctx: Context): String = assetText(ctx, "yuxia_memes.txt")

    private fun assetText(ctx: Context, name: String): String = runCatching {
        ctx.assets.open(name).bufferedReader().use { it.readText() }
    }.getOrDefault("")

    /** 启动内嵌服务；重复调用直接返回 */
    @Synchronized
    fun start(ctx: Context): Boolean {
        if (started) return true
        if (!Python.isStarted()) Python.start(AndroidPlatform(ctx.applicationContext))
        val root = File(ctx.filesDir, "yuxia")
        if (!root.exists()) root.mkdirs()
        val module = Python.getInstance().getModule("mobile_boot")
        val token = module.callAttr(
            "start", root.absolutePath, PORT, promptText(ctx), memesText(ctx),
        ).toString()
        Prefs.setBaseUrl(ctx, baseUrl())
        Prefs.setToken(ctx, token)
        started = true
        return true
    }
}
