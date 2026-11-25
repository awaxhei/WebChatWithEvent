package com.yuxia.chat

import android.content.Context
import android.net.Uri
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.MultipartBody
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.TimeUnit

class ApiException(val code: Int, message: String) : Exception(message)

/**
 * 后端接口。原生 App 全程走 Authorization: Bearer，不依赖 Cookie。
 */
object Api {
    private val JSON_MEDIA = "application/json; charset=utf-8".toMediaType()
    private val OCTET = "application/octet-stream".toMediaType()

    private val client: OkHttpClient = OkHttpClient.Builder()
        .connectTimeout(15, TimeUnit.SECONDS)
        .readTimeout(300, TimeUnit.SECONDS)   // 思考模式下的首个 token 可能很慢
        .writeTimeout(120, TimeUnit.SECONDS)
        .build()

    fun abs(base: String, path: String): String =
        if (path.startsWith("http")) path else base.trimEnd('/') + path

    private fun url(ctx: Context, path: String) = abs(Prefs.baseUrl(ctx), path)

    private fun builder(ctx: Context, path: String): Request.Builder {
        val b = Request.Builder().url(url(ctx, path))
        val token = Prefs.token(ctx)
        if (token.isNotEmpty()) b.header("Authorization", "Bearer $token")
        // DeepSeek Key 由 App 自己带着走（后端跑在本机，Key 不写进 APK）
        val key = Prefs.apiKey(ctx)
        if (key.isNotEmpty()) b.header("X-DeepSeek-API-Key", key)
        return b
    }

    private fun parse(resp: Response, raw: String): JSONObject {
        if (!resp.isSuccessful) {
            val msg = runCatching { JSONObject(raw).optString("error") }.getOrNull().orEmpty()
            throw ApiException(resp.code, msg.ifBlank { "HTTP ${resp.code}" })
        }
        return JSONObject(raw)
    }

    // ── 本机账号（单机模式不需要登录）──────────────────

    /** 只用来验证本机服务是否活着、token 是否有效 */
    fun ping(ctx: Context): JSONObject {
        val req = builder(ctx, "/api/ping").get().build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    // ── 会话 ─────────────────────────────────────────

    fun conversations(ctx: Context): JSONArray {
        val req = builder(ctx, "/api/history").get().build()
        client.newCall(req).execute().use { resp ->
            val raw = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                val msg = runCatching { JSONObject(raw).optString("error") }.getOrNull().orEmpty()
                throw ApiException(resp.code, msg.ifBlank { "HTTP ${resp.code}" })
            }
            return JSONArray(raw)
        }
    }

    fun newConversation(ctx: Context): String {
        val req = builder(ctx, "/api/history/new")
            .post(JSONObject().toString().toRequestBody(JSON_MEDIA)).build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty()).optString("id")
        }
    }

    fun history(ctx: Context, conv: String): JSONObject {
        val req = builder(ctx, "/api/history/$conv").get().build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    fun scene(ctx: Context, conv: String): JSONObject {
        val req = builder(ctx, "/api/scene/$conv").get().build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    fun pause(ctx: Context, conv: String, paused: Boolean): JSONObject {
        val body = JSONObject().put("paused", paused)
        val req = builder(ctx, "/api/pause/$conv")
            .post(body.toString().toRequestBody(JSON_MEDIA)).build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    fun move(ctx: Context, conv: String, who: String, location: String): JSONObject {
        val body = JSONObject().put("who", who).put("location", location)
        val req = builder(ctx, "/api/move/$conv")
            .post(body.toString().toRequestBody(JSON_MEDIA)).build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    /** 故事进展：事件列表 + 故事时间 */
    fun events(ctx: Context, conv: String): JSONObject {
        val req = builder(ctx, "/api/events/$conv").get().build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    /** 拨时间（只往前走，服务端最多允许 +3 天） */
    fun setTime(ctx: Context, conv: String, hour: Int, minute: Int): JSONObject {
        val body = JSONObject().put("hour", hour).put("minute", minute)
        val req = builder(ctx, "/api/time/$conv")
            .post(body.toString().toRequestBody(JSON_MEDIA)).build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    /** 行动速度 1..5（等待期仍然是递增的，最长 20 秒） */
    fun setSpeed(ctx: Context, conv: String, speed: Int): JSONObject {
        val body = JSONObject().put("speed", speed)
        val req = builder(ctx, "/api/speed/$conv")
            .post(body.toString().toRequestBody(JSON_MEDIA)).build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    fun deleteConversation(ctx: Context, conv: String) {
        val req = builder(ctx, "/api/history/$conv").delete().build()
        client.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) {
                val raw = resp.body?.string().orEmpty()
                val msg = runCatching { JSONObject(raw).optString("error") }.getOrNull().orEmpty()
                throw ApiException(resp.code, msg.ifBlank { "HTTP ${resp.code}" })
            }
        }
    }

    /** 批量删除：一次请求删掉多个会话 */
    fun deleteConversations(ctx: Context, ids: List<String>) {
        val body = JSONObject().put("ids", JSONArray(ids)).toString()
        val req = builder(ctx, "/api/history/delete")
            .post(body.toRequestBody(JSON_MEDIA))
            .build()
        client.newCall(req).execute().use { resp ->
            if (!resp.isSuccessful) {
                val raw = resp.body?.string().orEmpty()
                val msg = runCatching { JSONObject(raw).optString("error") }.getOrNull().orEmpty()
                throw ApiException(resp.code, msg.ifBlank { "HTTP ${resp.code}" })
            }
        }
    }

    // ── 手机通道 ─────────────────────────────────────

    fun phone(ctx: Context, conv: String): JSONObject {
        val req = builder(ctx, "/api/phone/$conv").get().build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    fun markPhoneRead(ctx: Context, conv: String) {
        val req = builder(ctx, "/api/phone/read/$conv")
            .post(JSONObject().toString().toRequestBody(JSON_MEDIA)).build()
        runCatching { client.newCall(req).execute().close() }
    }

    /** 导出整段会话（纯文本）：手机上把记录发出来调试 */
    fun exportChat(ctx: Context, conv: String): String {
        val req = builder(ctx, "/api/export/$conv").get().build()
        client.newCall(req).execute().use { resp ->
            val raw = resp.body?.string().orEmpty()
            if (!resp.isSuccessful) {
                val msg = runCatching { JSONObject(raw).optString("error") }.getOrNull().orEmpty()
                throw ApiException(resp.code, msg.ifBlank { "HTTP ${resp.code}" })
            }
            return raw
        }
    }

    // ── 表情包 ───────────────────────────────────────

    fun stickers(ctx: Context): JSONObject {
        val req = builder(ctx, "/api/stickers/list?limit=500").get().build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    /** 相册多选一键导入：一次请求带上所有图片 */
    suspend fun uploadStickers(ctx: Context, uris: List<Uri>): JSONObject = withContext(Dispatchers.IO) {
        val form = MultipartBody.Builder().setType(MultipartBody.FORM)
        var count = 0
        for ((index, uri) in uris.withIndex()) {
            val bytes = runCatching {
                ctx.contentResolver.openInputStream(uri)?.use { it.readBytes() }
            }.getOrNull() ?: continue
            if (bytes.isEmpty()) continue
            val name = uri.lastPathSegment?.substringAfterLast('/')?.takeIf { it.isNotBlank() }
                ?: "image_$index.png"
            form.addFormDataPart("files", name, bytes.toRequestBody(OCTET))
            count++
        }
        if (count == 0) throw ApiException(0, "没有读到图片")
        val req = builder(ctx, "/api/stickers/upload").post(form.build()).build()
        client.newCall(req).execute().use { resp ->
            parse(resp, resp.body?.string().orEmpty())
        }
    }

    // ── 后台通知 / 闲置 ──────────────────────────────

    fun notifyPending(ctx: Context, conv: String, sinceId: Long): JSONObject {
        val req = builder(ctx, "/api/notify/pending?conversation_id=$conv&since_id=$sinceId").get().build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    fun idle(ctx: Context, conv: String): JSONObject {
        val req = builder(ctx, "/api/idle/$conv")
            .post(JSONObject().toString().toRequestBody(JSON_MEDIA)).build()
        client.newCall(req).execute().use { resp ->
            return parse(resp, resp.body?.string().orEmpty())
        }
    }

    // ── 聊天（SSE 流式）─────────────────────────────

    /**
     * 发一条消息或一张表情包，边收边把事件交给 onEvent。
     * 事件类型：conv_id / chunk / story_time / scene_changed / yuxia_state /
     *          world_narration / autonomy / npc / done
     */
    suspend fun streamChat(ctx: Context, payload: JSONObject, onEvent: (JSONObject) -> Unit) =
        stream(ctx, "/api/chat", payload, onEvent)

    /** 一轮对话之后的世界推进（旁白、神态描写、她的自主行动） */
    suspend fun streamAtmosphere(ctx: Context, conv: String, msgCount: Int,
                                 onEvent: (JSONObject) -> Unit) =
        stream(ctx, "/api/atmosphere/$conv",
            JSONObject().put("msg_count", msgCount).put("is_push_cmd", false), onEvent)

    suspend fun stream(ctx: Context, path: String, payload: JSONObject,
                       onEvent: (JSONObject) -> Unit) =
        withContext(Dispatchers.IO) {
            val req = builder(ctx, path)
                .post(payload.toString().toRequestBody(JSON_MEDIA)).build()
            client.newCall(req).execute().use { resp ->
                if (!resp.isSuccessful) {
                    val raw = resp.body?.string().orEmpty()
                    val msg = runCatching { JSONObject(raw).optString("error") }.getOrNull().orEmpty()
                    throw ApiException(resp.code, msg.ifBlank { "HTTP ${resp.code}" })
                }
                val source = resp.body?.source() ?: throw ApiException(0, "连接中断")
                while (true) {
                    val line = source.readUtf8Line() ?: break
                    if (!line.startsWith("data:")) continue
                    val raw = line.removePrefix("data:").trim()
                    if (raw.isEmpty()) continue
                    val obj = runCatching { JSONObject(raw) }.getOrNull() ?: continue
                    onEvent(obj)
                }
            }
        }
}
