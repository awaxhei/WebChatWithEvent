package com.yuxia.chat

import android.app.Application
import android.net.Uri
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
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
    /** 服务端报这个分钟数时的时刻（毫秒）：界面上的钟据此自己往前走 */
    val clockAt: Long = 0L,
    val npcs: List<String> = emptyList(),
    val pushMode: Boolean = false,
    val moodFace: String = "(ᗜ ‸ ᗜ)",
    val speed: Int = 3,
    val yuxiaDesc: String = "",
    /** 手机面板是不是开着：开着的时候进来的消息不算未读 */
    val phoneOpen: Boolean = false,
    /** 内置后端自报的版本，用来核对它和 App 是同一份 */
    val backendBuild: BackendBuild = BackendBuild(),
)

class ChatViewModel(app: Application) : AndroidViewModel(app) {

    var state by mutableStateOf(
        UiState(baseUrl = Prefs.baseUrl(app), apiKey = Prefs.apiKey(app))
    )
        private set

    private var streamRaw = ""
    private var renderedObjects = 0
    private var lastMsgCount = 0
    private var sceneRetries = 0
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
        loadBackendBuild()
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

    /** 问一次后端自报的版本，和 App 内显示的版本对照 */
    fun loadBackendBuild() {
        viewModelScope.launch {
            val obj = runCatching { withContext(Dispatchers.IO) { Api.ping(app) } }.getOrNull()
                ?: return@launch
            state = state.copy(
                backendBuild = BackendBuild(
                    version = obj.optString("app_version", ""),
                    sha = obj.optString("build_sha", ""),
                    builtAt = obj.optString("build_at", ""),
                    online = true,
                )
            )
        }
    }

    private fun enterHome() {
        // 有上次的会话就直接进去（从通知点进来也是这条路）
        val last = Prefs.convId(app)
        if (last.isNotEmpty()) openConversation(last) else loadConversations(goToList = true)
    }

    fun saveApiKey(key: String) {
        val trimmed = key.trim()
        if (trimmed.isEmpty()) {
            state = state.copy(error = "API Key 不能为空")
            return
        }
        Prefs.setApiKey(app, trimmed)
        state = state.copy(apiKey = trimmed, error = null, busy = true)
        viewModelScope.launch {
            // 先打一次接口确认服务在跑，再进主界面
            val ok = runCatching { withContext(Dispatchers.IO) { Api.ping(app) } }.isSuccess
            state = state.copy(busy = false)
            if (ok) { loadBackendBuild(); enterHome() }
            else state = state.copy(error = "本机服务无响应，请稍后重试")
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
                state = state.copy(busy = false, error = e.message ?: "读取会话列表失败")
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
                state = state.copy(busy = false, error = e.message ?: "创建会话失败")
            }
        }
    }

    /** 回到前台：把历史、场景、事件重拉一遍
     *
     * 后台时通知可能已经先到了，聊天界面却还停在离开前的位置——
     * 这就是「通知比实际聊天早」的观感来源。回前台先对齐一次。
     */
    fun reloadOnResume() {
        val conv = state.convId
        if (conv.isEmpty() || state.screen != Screen.Chat) return
        viewModelScope.launch {
            try {
                val obj = withContext(Dispatchers.IO) { Api.history(app, conv) }
                applyHistory(obj)
                loadEvents()
                refreshScene()
            } catch (_: Exception) {
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
                state = state.copy(busy = false, error = e.message ?: "读取会话记录失败")
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
                    "phone" -> {
                        val speaker = row.optString("speaker").trim()
                        phoneEntries.add(
                            when {
                                role == "user" -> meEntry(content)
                                speaker.isNotEmpty() -> Entry.Npc(speaker, content)
                                else -> Entry.Her(Model.stripSpeaker(content), phone = true)
                            }
                        )
                    }
                    "state" -> lastState = content
                    "action" -> entries.add(Entry.Action(content))
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
                            // 动作模式下玩家发的是旁白（「（青梅把拖鞋放到你脚边。）」），
                            // 不是他说的话：右对齐的玩家动作，不是世界事件
                            if (content.trimStart().startsWith("（")) {
                                entries.add(Entry.PlayerAction(content))
                            } else {
                                entries.add(meEntry(content))
                            }
                        } else {
                            val reply = Model.parseReply(content)
                            reply.speeches.forEach {
                                entries.add(Entry.Her(it, face = Model.faceOf(reply.mood)))
                            }
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
            storyMinutes = obj.optInt("story_minutes", state.storyMinutes),
            clockAt = obj.optLong("sync_at", 0L).let { if (it > 0) it * 1000 else System.currentTimeMillis() },
            unread = if (state.phoneOpen) 0 else obj.optInt("unread", 0),
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
            yuxiaDesc = scene.optString("yuxia_description").ifBlank { scene.optString("description") },
            storyTime = scene.optString("story_time").ifBlank { state.storyTime },
            storyMinutes = scene.optInt("story_minutes", state.storyMinutes),
            clockAt = scene.optLong("sync_at", 0L).let {
                if (it > 0) it * 1000 else System.currentTimeMillis()
            },
            paused = scene.optBoolean("paused", state.paused),
            locations = if (names.isEmpty()) state.locations else names,
            npcs = npcNames,
            moodFace = scene.optString("mood_face").ifBlank { state.moodFace },
            speed = scene.optInt("speed", state.speed),
        )
        // 环境描写是后台生成的：pending 就隔一会儿再取一次（最多 6 次）
        if (scene.optBoolean("pending", false)) {
            if (sceneRetries < 6) {
                sceneRetries += 1
                viewModelScope.launch {
                    delay(1600)
                    refreshScene()				
                }
            }
        } else {
            sceneRetries = 0
        }
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
                state = state.copy(toast = e.message ?: "时间未调整")
            }
        }
    }

    fun deleteConversation(id: String) {
        deleteConversations(listOf(id))
    }

    /** 批量删除：删完回到列表；当前正在聊的那个也在里面就清空会话 */
    fun deleteConversations(ids: List<String>) {
        if (ids.isEmpty()) return
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { Api.deleteConversations(app, ids) }
                if (state.convId in ids) {
                    Prefs.setConvId(app, "")
                    state = state.copy(
                        convId = "", entries = emptyList(), phoneEntries = emptyList(),
                        events = emptyList(), eventsLoaded = false,
                    )
                }
                state = state.copy(toast = "已删除 ${ids.size} 个会话")
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
                appendEntry(false, Entry.Narration(if (together) "你与尤夏一同前往$location" else "你前往$location"))
            } catch (e: Exception) {
                state = state.copy(toast = e.message ?: "移动失败")
            }
        }
    }

    /** 行动速度 1..5：只影响她多久自己动一下，等待期仍然是递增的（上限 20 秒） */
    fun setSpeed(speed: Int) {
        val conv = state.convId
        if (conv.isEmpty()) return
        val value = speed.coerceIn(1, 5)
        state = state.copy(speed = value)
        viewModelScope.launch {
            try {
                withContext(Dispatchers.IO) { Api.setSpeed(app, conv, value) }
            } catch (_: Exception) {
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
        // 世界侧是另一个流：她的动作与神态、随机事件、事件面板都在里面。
        // 网页一直在跑这个流，App 以前根本没接，所以手机上永远看不到动作描写。
        viewModelScope.launch {
            try {
                Api.streamAtmosphere(app, conv, lastMsgCount) { ev ->
                    viewModelScope.launch(Dispatchers.Main) { applyEvent(ev, false) }
                }
            } catch (_: Exception) {
            }
        }
    }

    /** 动作模式：这一轮玩家做一件事（旁白），不开口；她像读到世界里的事一样接下去。 */
    fun sendPush(hint: String) {
        if (state.busy) return
        val body = hint.trim()
        val act = when {
            body.isBlank() -> "青梅轻轻碰了碰她"
            body.startsWith("我") -> "青梅" + body.removePrefix("我").trimStart()
            body.startsWith("青梅") || body.startsWith("尤夏") -> body
            else -> "青梅$body"
        }
        appendEntry(false, Entry.PlayerAction("（${act.trimEnd('。')}。）"))
        viewModelScope.launch { sendInternal("/动作 " + body, null, false, echo = false) }
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
                val minutes = ev.optInt("minutes", -1)
                val syncAt = ev.optLong("sync_at", 0L)
                state = state.copy(
                    storyTime = t.ifBlank { state.storyTime },
                    storyMinutes = if (minutes >= 0) minutes else state.storyMinutes,
                    clockAt = if (syncAt > 0) syncAt * 1000 else System.currentTimeMillis(),
                )
            }
            "scene_changed" -> applyScene(ev.optJSONObject("scene"))
            "npc" -> appendEntry(
                phone,
                Entry.Npc(ev.optString("name").ifBlank { "路人" }, ev.optString("content"))
            )
            "npc_phone" -> appendEntry(
                true,
                Entry.Npc(ev.optString("name").ifBlank { "对方" }, ev.optString("content"))
            )
            "world_narration" -> appendEntry(phone, Entry.Narration(ev.optString("content")))
            // 她的「动作」：这一轮她自己写下的动作，按发生顺序落在时间线上
            "yuxia_action" -> {
                val text = ev.optString("content")
                if (text.isNotBlank()) appendEntry(false, Entry.Action(text))
            }
            "yuxia_state" -> {
                val text = ev.optString("content")
                // 睡着/睁眼这类旁白是当面才看得见的事，手机通道里不该出现
                if (text.isNotBlank() && !phone) appendEntry(false, Entry.StateDesc(text))
            }
            "scene_discovered" -> {
                val where = ev.optString("location")
                if (where.isNotBlank()) {
                    appendEntry(false, Entry.Narration(
                        (if (ev.optBoolean("created")) "发现新场景：" else "进入场景：") + where))
                }
            }
            "story_summary" -> {
                val summary = ev.optString("content")
                if (summary.isNotBlank()) state = state.copy(storySummary = summary)
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
                // 服务端已经把"尤夏去了X"写好了，用它的；没有再退回泛泛的一句
                appendEntry(false, Entry.Narration(ev.optString("content").ifBlank { "尤夏离开了" }))
                applyScene(ev.optJSONObject("scene"))
            }
            "lead" -> {
                appendEntry(false, Entry.Narration(ev.optString("content").ifBlank { "尤夏带着你离开了" }))
                applyScene(ev.optJSONObject("scene"))
            }
            "act" -> if (ev.optBoolean("visible", true) && ev.optString("content").isNotBlank()) {
                // 这是她自己动手做的事，不是「世界」发生的事：用动作旁白显示
                appendEntry(false, Entry.Action(ev.optString("content")))
            }
            else -> {
                val text = ev.optString("content")
                if (text.isNotBlank()) {
                    val channel = ev.optString("channel")
                    appendEntry(channel == "phone", Entry.Her(Model.stripSpeaker(text), phone = channel == "phone"))
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
            val face = Model.faceOf(reply.mood)
            // 一句一个气泡（服务端也按同样的规则拆开存库，两边一致）
            reply.speeches.forEach { appendEntry(phone, Entry.Her(it, phone = phone, face = face)) }
            reply.stickerEmotions.forEach { emotion ->
                index[emotion]?.let { appendEntry(phone, Entry.HerSticker(it)) }
            }
            // 手机消息：她本人发的进手机面板；带说话人的是妈妈这类第三人发的
            reply.phoneMessages.forEach { (text, speaker) ->
                appendEntry(true, if (speaker.isNotBlank()) Entry.Npc(speaker, text)
                    else Entry.Her(Model.stripSpeaker(text), phone = true))
            }
        }
        renderedObjects = objects.size
    }

    /** 界面上的钟：按服务端给的分钟数 + 真实流逝重算一次（每 20 秒调一次） */
    fun tickClock() {
        if (state.storyMinutes <= 0) return
        val at = if (state.clockAt > 0) state.clockAt else System.currentTimeMillis()
        val extra = ((System.currentTimeMillis() - at) / 60000L).toInt()
        if (extra <= 0) return
        state = state.copy(storyTime = Model.formatStoryMinutes(state.storyMinutes + extra))
    }

    private fun appendEntry(phone: Boolean, entry: Entry) {
        state = if (phone) {
            // 面板开着就当她已经在看：不累计未读，顺手告诉服务端已读
            if (state.phoneOpen && entry is Entry.Her) {
                viewModelScope.launch { markPhoneReadQuietly() }
                state.copy(phoneEntries = state.phoneEntries + entry, unread = 0)
            } else {
                state.copy(phoneEntries = state.phoneEntries + entry)
            }
        } else {
            state.copy(entries = state.entries + entry)
        }
    }

    /** 手机面板开/关：开着的时候清掉未读并标已读 */
    fun setPhoneOpen(open: Boolean) {
        state = state.copy(phoneOpen = open, unread = if (open) 0 else state.unread)
        if (open) viewModelScope.launch { markPhoneReadQuietly() }
    }

    private suspend fun markPhoneReadQuietly() {
        val conv = state.convId
        if (conv.isEmpty()) return
        try {
            withContext(Dispatchers.IO) { Api.markPhoneRead(app, conv) }
        } catch (_: Exception) {
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
                        val speaker = row.optString("speaker").trim()
                        list.add(
                            when {
                                row.optString("role") == "user" -> meEntry(content)
                                speaker.isNotEmpty() -> Entry.Npc(speaker, content)
                                else -> Entry.Her(Model.stripSpeaker(content), phone = true)
                            }
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
                state = state.copy(toast = "已导入 $added 张（重复 $dup，已标注 $tagged）")
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
                    // 面板开着时服务端那边的未读数没有意义（他正在看），一律按 0
                    val unread = if (state.phoneOpen) 0 else res.optInt("unread", state.unread)
                    val minutes = res.optInt("story_minutes", -1)
                    state = state.copy(
                        unread = unread,
                        storyMinutes = if (minutes >= 0) minutes else state.storyMinutes,
                        clockAt = res.optLong("sync_at", 0L).let {
                            if (it > 0) it * 1000 else state.clockAt
                        },
                        storyTime = res.optString("story_time").ifBlank { state.storyTime },
                    )
                    if (state.phoneOpen) markPhoneReadQuietly()
                    return@launch
                }
                val action = res.optString("action")
                val scene = res.optJSONObject("scene")
                if (scene != null) applyScene(scene)
                val where = res.optString("location").trim()
                when (action) {
                    // 她自己换地方：以前这里没有分支，只有顶部的场景名悄悄变了，
                    // 屏幕上什么都没发生——看着就像"她根本没动"
                    "move" -> appendEntry(
                        false,
                        Entry.Narration(if (where.isNotEmpty()) "尤夏前往$where" else "尤夏离开了"),
                    )
                    "lead" -> appendEntry(
                        false,
                        Entry.Narration(if (where.isNotEmpty()) "尤夏带着你前往$where" else "尤夏带着你离开了"),
                    )
                    "act" -> if (res.optBoolean("visible", true) && res.optString("text").isNotBlank()) {
                        appendEntry(false, Entry.Action(res.optString("text")))
                    }
                    else -> {
                        val text = res.optString("text")
                        val channel = res.optString("channel")
                        if (text.isNotBlank()) {
                            appendEntry(channel == "phone", Entry.Her(Model.stripSpeaker(text), phone = channel == "phone"))
                        }
                    }
                }
                val time = res.optString("story_time")
                val minutes = res.optInt("story_minutes", -1)
                state = state.copy(
                    storyTime = time.ifBlank { state.storyTime },
                    storyMinutes = if (minutes >= 0) minutes else state.storyMinutes,
                    clockAt = res.optLong("sync_at", 0L).let {
                        if (it > 0) it * 1000 else System.currentTimeMillis()
                    },
                )
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

    /** 导出当前会话：拉一份纯文本，写进外部目录后交给系统分享（微信/文件管理器都收得到） */
    fun exportChat() {
        val conv = state.convId
        if (conv.isEmpty()) {
            state = state.copy(toast = "先开始一段对话")
            return
        }
        viewModelScope.launch {
            val text = runCatching {
                withContext(Dispatchers.IO) { Api.exportChat(app, conv) }
            }.getOrNull()
            if (text == null) {
                state = state.copy(toast = "导出失败，请稍后再试")
                return@launch
            }
            val dir = app.getExternalFilesDir(null) ?: app.cacheDir
            val file = java.io.File(dir, "yuxia-$conv.txt")
            runCatching { withContext(Dispatchers.IO) { file.writeText(text) } }
            val uri = runCatching {
                androidx.core.content.FileProvider.getUriForFile(
                    app, "com.yuxia.chat.fileprovider", file)
            }.getOrNull()
            val share = android.content.Intent(android.content.Intent.ACTION_SEND).apply {
                type = "text/plain"
                putExtra(android.content.Intent.EXTRA_SUBJECT, "尤夏 · 会话导出")
                putExtra(android.content.Intent.EXTRA_TEXT, text)
                if (uri != null) {
                    putExtra(android.content.Intent.EXTRA_STREAM, uri)
                    addFlags(android.content.Intent.FLAG_GRANT_READ_URI_PERMISSION)
                }
            }
            val chooser = android.content.Intent.createChooser(share, "导出会话").apply {
                addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK)
            }
            runCatching { app.startActivity(chooser) }
            state = state.copy(toast = "已导出：${file.name}")
        }
    }

    fun clearError() {
        state = state.copy(error = null)
    }

    fun backToConversations() {
        state = state.copy(screen = Screen.Conversations)
        loadConversations()
    }
}
