package com.yuxia.chat

import android.content.Context
import android.content.SharedPreferences

/**
 * 本地保存的东西：服务器地址、令牌、当前会话、通知游标。
 */
object Prefs {
    private const val NAME = "yuxia_prefs"
    private const val K_BASE = "base_url"
    private const val K_TOKEN = "token"
    private const val K_USER = "username"
    private const val K_CONV = "conv_id"
    private const val K_SEEN = "last_seen_id"
    private const val K_NOTIFY = "notify_enabled"

    private fun sp(c: Context): SharedPreferences = c.getSharedPreferences(NAME, Context.MODE_PRIVATE)

    fun baseUrl(c: Context): String = sp(c).getString(K_BASE, "").orEmpty()
    fun setBaseUrl(c: Context, v: String) {
        var url = v.trim()
        if (url.isNotEmpty() && !url.startsWith("http")) url = "http://$url"
        sp(c).edit().putString(K_BASE, url.trimEnd('/')).apply()
    }

    fun token(c: Context): String = sp(c).getString(K_TOKEN, "").orEmpty()
    fun setToken(c: Context, v: String) = sp(c).edit().putString(K_TOKEN, v).apply()

    fun username(c: Context): String = sp(c).getString(K_USER, "").orEmpty()
    fun setUsername(c: Context, v: String) = sp(c).edit().putString(K_USER, v).apply()

    fun convId(c: Context): String = sp(c).getString(K_CONV, "").orEmpty()
    fun setConvId(c: Context, v: String) = sp(c).edit().putString(K_CONV, v).apply()

    fun lastSeenId(c: Context): Long = sp(c).getLong(K_SEEN, 0L)
    fun setLastSeenId(c: Context, v: Long) = sp(c).edit().putLong(K_SEEN, v).apply()

    fun notifyEnabled(c: Context): Boolean = sp(c).getBoolean(K_NOTIFY, true)
    fun setNotifyEnabled(c: Context, v: Boolean) = sp(c).edit().putBoolean(K_NOTIFY, v).apply()

    fun logout(c: Context) {
        sp(c).edit().remove(K_TOKEN).remove(K_CONV).remove(K_SEEN).apply()
    }
}
