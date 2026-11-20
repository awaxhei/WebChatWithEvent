package com.yuxia.chat

import android.app.Application
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONObject

enum class Screen { Boot, ApiKey, Conversations, Chat }

data class UiState(
    val screen: Screen = Screen.Boot,
    val baseUrl: String = "",
    val apiKey: String = "",
    val busy: Boolean = false,
    val error: String? = null,
    val convs: List<ConvBrief> = emptyList(),
    val convId: String = "",
    val entries: List<Entry> = emptyList(),
    val phoneEntries: List<Entry> = emptyList(),
    val streaming: Boolean = false,
    val location: String = "",
    val playerLocation: String = "",
    val coLocated: Boolean = true,
    val sceneDesc: String = "",
    val storyTime: String = "",
    val paused: Boolean = false,
    val locations: List<String> = emptyList(),
    val stickers: List<StickerItem> = emptyList(),
    val emotionIndex: Map<String, String> = emptyMap(),
    val unread: Int = 0,
    val importing: Boolean = false,
    val toast: String? = null,
    val events: List<EventItem> = emptyList(),
    val storySummary: String = "",
    val eventsLoaded: Boolean = false,
    val storyMinutes: Int = 0,
    val npcs: List<String> = emptyList(),
    val pushMode: Boolean = false,
)

class ChatViewModel(app: Application) : AndroidViewModel(app) {

    var state by mutableStateOf(
        UiState(baseUrl = Prefs.baseUrl(app), apiKey = Prefs.apiKey(app))
    )
        private set

    private var streamRaw = ""
    private var renderedObjects = 0
    private var lastMsgCount = 0
    private val app get() = getApplication<Application>()

    // ── 启动 ────────────────────────────────────────

    /** 内嵌 Python 后端起来了（或失败了），决定第一屏 */
    fun onBackendReady(ok: Boolean, reason: String = "") {
        if (!ok) {
            state = state.copy(
                screen = Screen.Boot,
                error = "本机服务启动失败：" + reason.ifBlank { "未知原因" },
            )
            return
        }
        state = state.copy(baseUrl = Prefs.baseUrl(app), error = null)
        if (Prefs.apiKey(app).isEmpty()) {
            state = state.copy(screen = Screen.ApiKey)
            return
        }
        enterHome()
    }

    /** 重新尝试启动（启动失败时的按钮） */
    fun retryBoot() {
        state = state.copy(screen = Screen.Boot, error = null)
    }

    private fun enterHome() {
        // 有上次的会话就直接进去（从通知点进来也是这条路）
        val last = Prefs.convId(app)
        if (last.isNotEmpty()) openConversation(last) else loadConversations(goToList = true)
    }

    fun saveApiKey(key: String) {
        val trimmed = key.trim()
        if (trimmed.isEmpty()) {
            state = state.copy(error = "Key 不能为空")
            return
        }
        Prefs.setApiKey(app, trimmed)
        state = state.copy(apiKey = trimmed, error = null, busy = true)
        viewModelScope.launch {
            // 先打一次接口确认服务在跑，再进主界面
            val ok = runCatching { withContext(Dispatchers.IO) { Api.ping(app) } }.isSuccess
            state = state.copy(busy = false)
            if (ok) enterHome()
            else state = state.copy(error = "本机服务没响应，稍后重试")
        }
    }

    fun openApiKeyScreen() {
        state = state.copy(screen = Screen.ApiKey, error = null)
    }

    /** Key 页面的"用原来的 Key，先回去" */
    fun enterHomeFromKeyScreen() {
        state = state.copy(screen = Screen.Conversations, error = null)
        enterHome()
    }

    // ── 会话 ────────────────────────────────────────

    fun loadConversations(goToList: Boolean = false) {
        viewModelScope.launch {
            state = state.copy(busy = true, error = null)
            try {
                val list = withContext(Dispatchers.IO) {
                    Model.conversations(Api.conversations(app))
                }
                state = state.copy(
                    busy = false,
                    convs = list,
                    screen = if (goToList) Screen.Conversations else state.screen,
                )
            } catch (e: Exception) {
                state = state.copy(busy = false, error = e.message ?: "读取会话失败")
            }
        }
    }

    fun newConversation() {
        viewModelScope.launch {
            state = state.copy(busy = true, error = null)
            try {
                val id = withContext(Dispatchers.IO) { Api.newConversation(app) }
                state = state.copy(busy = false)
                openConversation(id)
            } catch (e: Exception) {
                state = state.copy(busy = false, error = e.message ?: "新建会话失败")
            }
        }
    }

    fun openConversation(id: String) {
        viewModelScope.launch {
            Prefs.setConvId(app, id)
            state = state.copy(
                screen = Screen.Chat, convId = id, busy = true, error = null,
                entries = emptyList(), phoneEntries = emptyList(),
            )
            try {
                loadStickersNow()
                val history = withContext(Dispatchers.IO) { Api.history(app, id) }
                applyHistory(history)
                withContext(Dispatchers.IO) {
                    Api.markPhoneRead(app, id)
                    noteSeen()
                }
                state = state.copy(busy = false, unread = 0)
            } catch (e: Exception) {
                state = state.copy(busy = false, error = e.message ?: "读取对话失败")
            }
        }
    }

    private fun applyHistory(obj: JSONObject) {
        val entries = ArrayList<Entry>()
        val phoneEntries = ArrayList<Entry>()
        var lastState = ""
        val messages = obj.optJSONArray("messages")
        if (messages != null) {
            for (i in 0 until messages.length()) {
                val row = messages.optJSONObject(i) ?: continue
                val channel = row.optString("channel").ifBlank { "chat" }
                val role = row.optString("role")
                val content = row.optString("content")
                when (channel) {
                    "phone" -> phoneEntries.add(if (role == "user") meEntry(content) else Entry.Her(content, phone = true))
                    "state" -> lastState = content
                    "npc" -> {
                        val npc = runCatching { JSONObject(content) }.getOrNull()
                        entries.add(
                            Entry.Npc(
                                npc?.optString("name").orEmpty().ifBlank { "路人" },
                                npc?.optString("text").orEmpty(),
                            )
                        )
                    }
                    else -> {
                        if (role == "user") {
                            entries.add(meEntry(content))
                        } else {
                            val reply = Model.parseReply(content)
                            reply.speeches.forEach { entries.add(Entry.Her(it)) }
                            reply.stickerEmotions.forEach { emotion ->
                                state.emotionIndex[emotion]?.let { entries.add(Entry.HerSticker(it)) }
                            }
                        }
                    }
                }
            }
        }
        if (lastState.isNotBlank()) entries.add(Entry.StateDesc(lastState))

        state = state.copy(
            entries = entries,
            phoneEntries = phoneEntries,
            location = obj.optString("location"),
            playerLocation = obj.optString("player_location"),
            coLocated = obj.optBoolean("co_located", true),
            storyTime = obj.optString("story_time"),
            unread = obj.optInt("unread", 0),
        )
        refreshScene()
    }

    private fun meEntry(content: String): Entry {
        val sticker = Model.stickerUrlOf(content)
        return if (sticker != null) Entry.Me(stickerUrl = sticker) else Entry.Me(text = content)
    }

    // ── 场景 ────────────────────────────────────────

    fun refreshScene() {
        val conv = state.convId
        if (conv.isEmpty()) return
        viewModelScope.launch {
            try {
                val scene = withContext(Dispatchers.IO) { Api.scene(app, conv) }
                applyScene(scene)
            } catch (_: Exception) {
            }
        }
    }

    private fun applyScene(scene: JSONObject?) {
        if (scene == null) return
        val names = ArrayList<String>()
        scene.optJSONArray("locations")?.let { arr ->
            for (i in 0 until arr.length()) {
                val name = arr.optString(i)
                if (name.isNotBlank()) names.add(name)
            }
        }
        val npcNames = ArrayList<String>()
        scene.optJSONArray("npcs")?.let { arr ->
            for (i in 0 until arr.length()) {
                val npc = arr.optJSONObject(i) ?: continue
                val name = npc.optString("name")
                if (name.isNotBlank()) npcNames.add(name)
            }
        }
        state = state.copy(
            location = scene.optString("location"),
            playerLocation = scene.optString("player_location"),
            coLocated = scene.optBoolean("co_located", true),
            sceneDesc = scene.optString("player_description").ifBlank { scene.optString("description") },
            storyTime = scene.optString("story_time").ifBlank { state.storyTime },
            storyMinutes = scene.optInt("story_minutes", state.storyMinutes),
            paused = scene.optBoolean("paused", state.paused),
            locations = if (names.isEmpty()) state.locations else names,
            npcs = npcNames,
        )
    }

    // ── 故事进展 / 时间 / 会话管理 ───────────────────

    /** 故事进展面板的数据（和网页同一个接口） */
    fun loadEvents() {
        val conv = state.convId
        if (conv.isEmpty()) return
        viewModelScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { Api.events(app, conv) }
                state = state.copy(
                    events = Model.events(res),
                    storySummary = res.optString("story_summary").ifBlank { state.storySummary },
                    eventsLoaded = true,
                    storyTime = res.optString("story_time").ifBlank { state.storyTime },
                )
            } catch (_: Exception) {
            }
        }
    }

    /** 拨时间表盘：和网页一样只往前走、一次最多三天（服务端校验） */
    fun setTime(hour: Int, minute: Int) {
        val conv = state.convId
        if (conv.isEmpty()) return
        viewModelScope.launch {
            try {
                val scene = withContext(Dispatchers.IO) { Api.setTime(app, conv, hour, minute) }
                applyScene(scene)
                val from = scene.optString("from")
                val to = scene.optString("to")
                if (from.isNotBlank() && to.isNotBlank()) {
                    appendEntry(false, Entry.Narration("时间流转：$from → $to"))
                }
                loadEvents()
            } catch (e: Exception) {
                state = state.copy(toast = e.message ?: "时间没拨动")
            }
        }
    }

    fun deleteConversation(id: String) {
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { Api.deleteConversation(app, id) }
                if (state.convId == id) {
                    Prefs.setConvId(app, "")
                    state = state.copy(
                        convId = "", entries = emptyList(), phoneEntries = emptyList(),
                        events = emptyList(), eventsLoaded = false,
                    )
                }
                loadConversations(goToList = true)
            } catch (e: Exception) {
                state = state.copy(toast = e.message ?: "删除失败")
            }
        }
    }

    fun togglePushMode() {
        state = state.copy(pushMode = !state.pushMode)
    }

    fun moveTo(location: String, together: Boolean) {
        val conv = state.convId
        if (conv.isEmpty() || location.isBlank()) return
        viewModelScope.launch {
            try {
                val payload = withContext(Dispatchers.IO) {
                    Api.move(app, conv, if (together) "both" else "player", location)
                }
                applyScene(payload)
                appendEntry(false, Entry.Narration(if (together) "你和尤夏一起走到了$location" else "你走到了$location"))
            } catch (e: Exception) {
                state = state.copy(toast = e.message ?: "移动失败")
            }
        }
    }

    fun togglePause() {
        val conv = state.convId
        if (conv.isEmpty()) return
        val next = !state.paused
        state = state.copy(paused = next)
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { Api.pause(app, conv, next) }
            } catch (_: Exception) {
            }
        }
    }

    // ── 发消息 ──────────────────────────────────────

    fun send(text: String, viaPhone: Boolean = false) {
        val body = text.trim()
        if (body.isEmpty() || state.busy) return
        viewModelScope.launch { sendInternal(body, null, viaPhone) }
    }

    fun sendSticker(item: StickerItem, viaPhone: Boolean = false) {
        if (state.busy) return
        viewModelScope.launch { sendInternal(null, item, viaPhone) }
    }

    private suspend fun sendInternal(text: String?, sticker: StickerItem?, viaPhone: Boolean,
                                     echo: Boolean = true) {
        val conv = state.convId.ifEmpty { return }
        val phone = viaPhone || !state.coLocated
        if (echo) {
            val mine = if (sticker != null) Entry.Me(stickerUrl = sticker.url) else Entry.Me(text = text.orEmpty())
            appendEntry(phone, mine)
        }
        streamRaw = ""
        renderedObjects = 0
        state = state.copy(busy = true, streaming = true, error = null)

        val payload = JSONObject().put("conversation_id", conv)
        if (sticker != null) payload.put("sticker", sticker.path) else payload.put("message", text.orEmpty())
        if (viaPhone) payload.put("channel", "phone")

        try {
            Api.streamChat(app, payload) { ev ->
                viewModelScope.launch(Dispatchers.Main) { applyEvent(ev, phone) }
            }
        } catch (e: Exception) {
            state = state.copy(busy = false, streaming = false, error = e.message ?: "发送失败")
            return
        }
        state = state.copy(busy = false, streaming = false, unread = 0)
        streamRaw = ""
        viewModelScope.launch { withContext(Dispatchers.IO) { noteSeen() } }
        loadEvents()
    }

    /** 故事推进模式：不发"我"的气泡，只提示一句，然后让她接着演 */
    fun sendPush(hint: String) {
        if (state.busy) return
        val body = hint.trim()
        appendEntry(false, Entry.Narration(if (body.isBlank()) "（轻戳尤夏）" else body))
        viewModelScope.launch { sendInternal("/推进 " + body, null, false, echo = false) }
    }

    private fun applyEvent(ev: JSONObject, phone: Boolean) {
        when (ev.optString("type")) {
            "conv_id" -> {
                val id = ev.optString("id")
                if (id.isNotEmpty() && id != state.convId) {
                    Prefs.setConvId(app, id)
                    state = state.copy(convId = id)
                }
            }
            "chunk" -> {
                streamRaw += ev.optString("content")
                renderStreamed(phone)
            }
            "story_time" -> {
                val t = ev.optString("content")
                if (t.isNotEmpty()) state = state.copy(storyTime = t)
            }
            "scene_changed" -> applyScene(ev.optJSONObject("scene"))
            "npc" -> appendEntry(
                phone,
                Entry.Npc(ev.optString("name").ifBlank { "路人" }, ev.optString("content"))
            )
            "world_narration" -> appendEntry(phone, Entry.Narration(ev.optString("content")))
            "yuxia_state" -> {
                val text = ev.optString("content")
                if (text.isNotBlank()) appendEntry(phone, Entry.StateDesc(text))
            }
            "autonomy" -> handleAutonomy(ev)
            "event_update" -> {
                // 世界引擎推来的最新事件（和网页右侧面板同一个数据源）
                if (ev.has("events")) {
                    state = state.copy(events = Model.events(ev), eventsLoaded = true)
                }
            }
            "done" -> {
                lastMsgCount = ev.optInt("msg_count", lastMsgCount)
                if (ev.has("channel")) {
                    state = state.copy(coLocated = ev.optBoolean("co_located", state.coLocated))
                }
            }
        }
    }

    private fun handleAutonomy(ev: JSONObject) {
        when (ev.optString("action")) {
            "move" -> {
                appendEntry(false, Entry.Narration("尤夏走开了"))
                applyScene(ev.optJSONObject("scene"))
            }
            "lead" -> {
                appendEntry(false, Entry.Narration(ev.optString("content").ifBlank { "尤夏拉着你走了" }))
                applyScene(ev.optJSONObject("scene"))
            }
            "act" -> if (ev.optBoolean("visible", true) && ev.optString("content").isNotBlank()) {
                appendEntry(false, Entry.Narration(ev.optString("content")))
            }
            else -> {
                val text = ev.optString("content")
                if (text.isNotBlank()) {
                    val channel = ev.optString("channel")
                    appendEntry(channel == "phone", Entry.Her(text, phone = channel == "phone"))
                }
            }
        }
    }

    /** 边流式边把已经完整的 JSON 渲染出来 */
    private fun renderStreamed(phone: Boolean) {
        val objects = Model.extractJsonObjects(streamRaw)
        if (objects.size <= renderedObjects) return
        val index = Model.emotionIndex(state.stickers)
        for (i in renderedObjects until objects.size) {
            val reply = Model.parseReply(objects[i])
            reply.speeches.forEach { appendEntry(phone, Entry.Her(it, phone = phone)) }
            reply.stickerEmotions.forEach { emotion ->
                index[emotion]?.let { appendEntry(phone, Entry.HerSticker(it)) }
            }
        }
        renderedObjects = objects.size
    }

    private fun appendEntry(phone: Boolean, entry: Entry) {
        state = if (phone) {
            state.copy(phoneEntries = state.phoneEntries + entry)
        } else {
            state.copy(entries = state.entries + entry)
        }
    }

    // ── 手机通道 ────────────────────────────────────

    fun loadPhone(markRead: Boolean = true) {
        val conv = state.convId
        if (conv.isEmpty()) return
        viewModelScope.launch {
            try {
                val payload = withContext(Dispatchers.IO) { Api.phone(app, conv) }
                val arr = payload.optJSONArray("messages")
                val list = ArrayList<Entry>()
                if (arr != null) {
                    for (i in 0 until arr.length()) {
                        val row = arr.optJSONObject(i) ?: continue
                        val content = row.optString("content")
                        list.add(
                            if (row.optString("role") == "user") meEntry(content)
                            else Entry.Her(content, phone = true)
                        )
                    }
                }
                state = state.copy(
                    phoneEntries = list,
                    unread = payload.optInt("unread", 0),
                    coLocated = payload.optBoolean("co_located", state.coLocated),
                )
                if (markRead) {
                    withContext(Dispatchers.IO) { Api.markPhoneRead(app, conv) }
                    state = state.copy(unread = 0)
                }
            } catch (_: Exception) {
            }
        }
    }

    // ── 表情包 ──────────────────────────────────────

    private suspend fun loadStickersNow() {
        try {
            val obj = withContext(Dispatchers.IO) { Api.stickers(app) }
            val items = Model.stickers(obj)
            state = state.copy(stickers = items, emotionIndex = Model.emotionIndex(items))
        } catch (_: Exception) {
        }
    }

    fun refreshStickers() {
        viewModelScope.launch { loadStickersNow() }
    }

    fun importStickers(uris: List<Uri>) {
        if (uris.isEmpty()) return
        viewModelScope.launch {
            state = state.copy(importing = true, toast = null)
            try {
                val res = withContext(Dispatchers.IO) { Api.uploadStickers(app, uris) }
                val added = res.optInt("uploaded_count")
                val dup = res.optInt("duplicates")
                val tagged = res.optInt("tagged")
                state = state.copy(toast = "导入 $added 张（重复 $dup，已打标 $tagged）")
                loadStickersNow()
            } catch (e: Exception) {
                state = state.copy(toast = "导入失败：${e.message}")
            } finally {
                state = state.copy(importing = false)
            }
        }
    }

    // ── 闲置轮询（她在页面上自己动）──────────────────

    fun pollIdle() {
        val conv = state.convId
        if (conv.isEmpty() || state.paused || state.busy) return
        viewModelScope.launch {
            try {
                val res = withContext(Dispatchers.IO) { Api.idle(app, conv) }
                if (!res.optBoolean("acted")) {
                    state = state.copy(unread = res.optInt("unread", state.unread))
                    return@launch
                }
                val action = res.optString("action")
                val scene = res.optJSONObject("scene")
                if (scene != null) applyScene(scene)
                when (action) {
                    "act" -> if (res.optBoolean("visible", true) && res.optString("text").isNotBlank()) {
                        appendEntry(false, Entry.Narration(res.optString("text")))
                    }
                    else -> {
                        val text = res.optString("text")
                        val channel = res.optString("channel")
                        if (text.isNotBlank()) {
                            appendEntry(channel == "phone", Entry.Her(text, phone = channel == "phone"))
                        }
                    }
                }
                val time = res.optString("story_time")
                if (time.isNotEmpty()) state = state.copy(storyTime = time)
                withContext(Dispatchers.IO) { noteSeen() }
            } catch (_: Exception) {
            }
        }
    }

    /** 把"已经看过"的游标推到最后，后台通知服务就不会再重复提醒 */
    private suspend fun noteSeen() {
        val conv = state.convId.ifEmpty { Prefs.convId(app) }
        if (conv.isEmpty()) return
        try {
            val res = Api.notifyPending(app, conv, Prefs.lastSeenId(app))
            val last = res.optLong("last_id", 0)
            if (last > 0) Prefs.setLastSeenId(app, last)
        } catch (_: Exception) {
        }
    }

    fun clearToast() {
        state = state.copy(toast = null)
    }

    fun clearError() {
        state = state.copy(error = null)
    }

    fun backToConversations() {
        state = state.copy(screen = Screen.Conversations)
        loadConversations()
    }
}
