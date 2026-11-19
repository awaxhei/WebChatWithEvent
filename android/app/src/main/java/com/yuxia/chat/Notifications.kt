package com.yuxia.chat

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent

object Notifications {
    const val CH_MSG = "yuxia_msg"
    const val CH_BG = "yuxia_bg"
    private const val FG_ID = 1001

    fun ensureChannels(ctx: Context) {
        val nm = ctx.getSystemService(NotificationManager::class.java) ?: return
        if (nm.getNotificationChannel(CH_MSG) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CH_MSG, "尤夏的消息", NotificationManager.IMPORTANCE_HIGH).apply {
                    description = "她发消息时的提醒"
                    enableVibration(true)
                }
            )
        }
        if (nm.getNotificationChannel(CH_BG) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CH_BG, "后台关注", NotificationManager.IMPORTANCE_MIN).apply {
                    description = "在后台等她消息时显示的低优先级常驻通知"
                    setShowBadge(false)
                }
            )
        }
    }

    private fun openIntent(ctx: Context, conv: String): PendingIntent {
        val intent = Intent(ctx, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP
            putExtra("conversation_id", conv)
        }
        return PendingIntent.getActivity(
            ctx, 9001, intent,
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE
        )
    }

    /** 一条"她说话了"的通知 */
    fun message(ctx: Context, conv: String, title: String, body: String) {
        ensureChannels(ctx)
        val notification = Notification.Builder(ctx, CH_MSG)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(title)
            .setContentText(body)
            .setStyle(Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true)
            .setContentIntent(openIntent(ctx, conv))
            .build()
        runCatching {
            ctx.getSystemService(NotificationManager::class.java)
                ?.notify((System.currentTimeMillis() % 100000).toInt(), notification)
        }
    }

    /** 前台服务的常驻通知（低优先级，不打扰） */
    fun foreground(ctx: Context, conv: String): Notification {
        ensureChannels(ctx)
        return Notification.Builder(ctx, CH_BG)
            .setSmallIcon(R.drawable.ic_notification)
            .setContentTitle("尤夏")
            .setContentText("在后台等着她的消息…")
            .setOngoing(true)
            .setContentIntent(openIntent(ctx, conv))
            .build()
    }

    const val FG_ID_PUBLIC = FG_ID
}
