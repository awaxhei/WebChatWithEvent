package com.yuxia.chat

import android.app.Service
import android.content.Context
import android.content.Intent
import android.os.IBinder
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject

/**
 * 退到后台之后，谁去问"她有没有说话"？
 *
 * 前端页面被系统挂起后 WebSocket/SSE 都断了，这里用一个前台服务每隔十几秒问一次后端：
 * 1. POST /api/idle        —— 给她一次自主行动的机会（服务端会按等待期判断，多数时候不花钱）
 * 2. GET  /api/notify/pending?since_id= —— 取她新说的话，弹系统通知
 */
class NotifyService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var loop: Job? = null

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onCreate() {
        super.onCreate()
        val conv = Prefs.convId(this)
        startForeground(Notifications.FG_ID_PUBLIC, Notifications.foreground(this, conv))
        loop = scope.launch {
            // 后台被系统重启时，内嵌后端可能还没起（Flask 在进程里）
            runCatching { Backend.start(applicationContext) }
            run()
        }
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int = START_STICKY

    override fun onDestroy() {
        loop?.cancel()
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun run() {
        while (scope.isActive) {
            val conv = Prefs.convId(this)
            if (conv.isNotEmpty() && Prefs.notifyEnabled(this)) {
                runCatching { Api.idle(this, conv) }
                runCatching { poll(conv) }
            }
            delay(8_000)
        }
    }

    private fun poll(conv: String) {
        val res = Api.notifyPending(this, conv, Prefs.lastSeenId(this))
        val arr = res.optJSONArray("messages") ?: return
        var newest = Prefs.lastSeenId(this)
        for (i in 0 until arr.length()) {
            val msg: JSONObject = arr.optJSONObject(i) ?: continue
            val id = msg.optLong("id", 0)
            if (id > newest) newest = id
            val text = msg.optString("text")
            val sticker = msg.optString("sticker")
            val channel = msg.optString("channel")
            val body = when {
                text.isNotBlank() -> text
                sticker.isNotBlank() -> "发来一个表情包"
                else -> continue
            }
            Notifications.message(
                this, conv,
                if (channel == "phone") "尤夏 · 手机" else "尤夏",
                body,
            )
        }
        val last = res.optLong("last_id", 0)
        if (last > newest) newest = last
        if (newest > 0) Prefs.setLastSeenId(this, newest)
    }

    companion object {
        fun start(ctx: Context) {
            runCatching {
                val intent = Intent(ctx, NotifyService::class.java)
                if (android.os.Build.VERSION.SDK_INT >= 26) ctx.startForegroundService(intent)
                else ctx.startService(intent)
            }
        }

        fun stop(ctx: Context) {
            runCatching { ctx.stopService(Intent(ctx, NotifyService::class.java)) }
        }
    }
}
