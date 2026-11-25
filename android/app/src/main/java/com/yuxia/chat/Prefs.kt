package com.yuxia.chat

import android.content.Context
import android.content.SharedPreferences

/**
 * 本地保存的东西：令牌、当前会话、通知游标、DeepSeek API Key。
 * 服务地址固定是 127.0.0.1（后端就跑在本机），不用再填。
 */
object Prefs {
    private const val NAME = "yuxia_prefs"
    private const val K_BASE = "base_url"
    private const val K_TOKEN = "token"
    private const val K_CONV = "conv_id"
    private const val K_SEEN = "last_seen_id"
    private const val K_NOTIFY = "notify_enabled"
    private const val K_API_KEY = "api_key"
    private const val K_VERSION = "last_version"

    private fun sp(c: Context): SharedPreferences = c.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun baseUrl(c: Context): String =
        sp(c).getString(K_BASE, "").orEmpty().ifBlank { Backend.baseUrl() }

    fun setBaseUrl(c: Context, v: String) = sp(c).edit().putString(K_BASE, v.trimEnd('/')).apply()

    fun token(c: Context): String = sp(c).getString(K_TOKEN, "").orEmpty()
    fun setToken(c: Context, v: String) = sp(c).edit().putString(K_TOKEN, v).apply()

    fun apiKey(c: Context): String = sp(c).getString(K_API_KEY, "").orEmpty()
    fun setApiKey(c: Context, v: String) = sp(c).edit().putString(K_API_KEY, v.trim()).apply()

    fun convId(c: Context): String = sp(c).getString(K_CONV, "").orEmpty()
    fun setConvId(c: Context, v: String) = sp(c).edit().putString(K_CONV, v).apply()

    fun lastSeenId(c: Context): Long = sp(c).getLong(K_SEEN, 0L)
    fun setLastSeenId(c: Context, v: Long) = sp(c).edit().putLong(K_SEEN, v).apply()

    fun notifyEnabled(c: Context): Boolean = sp(c).getBoolean(K_NOTIFY, true)
    fun setNotifyEnabled(c: Context, v: Boolean) = sp(c).edit().putBoolean(K_NOTIFY, v).apply()

    /** 上次运行的版本号：覆盖安装后第一次启动要提示更新了什么 */
    fun lastVersion(c: Context): String = sp(c).getString(K_VERSION, "").orEmpty()
    fun setLastVersion(c: Context, v: String) = sp(c).edit().putString(K_VERSION, v).apply()
}
