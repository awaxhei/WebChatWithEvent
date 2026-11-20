package com.yuxia.chat

import org.json.JSONArray
import org.json.JSONObject

data class ConvBrief(val id: String, val title: String, val updatedAt: String)

data class StickerItem(
    val path: String,
    val url: String,
    val emotion: String,
    val bucket: String = "",
    val caption: String,
)

/** 故事进展面板里的一条事件 */
data class EventItem(
    val id: Long,
    val type: String,
    val title: String,
    val desc: String,
    val status: String,
    val storyTime: String,
) {
    /** "第一天 07:03" → "第一天"，用来按天分组 */
    val day: String
        get() = Regex("第[^\\s]+天").find(storyTime)?.value ?: "未分组"

    /** "第一天 07:03" → "07:03" */
    val clock: String
        get() = Regex("\\d{1,2}:\\d{2}").find(storyTime)?.value ?: ""
}

/** 聊天里的一行 */
sealed interface Entry {
    /** 我说的（图片贴纸时 text 为空） */
    data class Me(val text: String = "", val stickerUrl: String = "") : Entry

    /** 她当面说的 / 手机里发的 */
    data class Her(val text: String, val phone: Boolean = false,
                     val face: String = "") : Entry

    /** 她发的表情包 */
    data class HerSticker(val url: String) : Entry

    /** 世界旁白（随机事件、换场景） */
    data class Narration(val text: String) : Entry

    /** 两人的动作神态描写 */
    data class StateDesc(val text: String) : Entry

    /** 现场 NPC 说的话 */
    data class Npc(val name: String, val text: String) : Entry
}

object Model {

    /** 玩家发的图片消息：{"表情包": 路径, "url": 地址, "desc": 说明} */
    fun stickerUrlOf(content: String): String? {
        val raw = content.trim()
        if (!raw.startsWith("{")) return null
        val obj = runCatching { JSONObject(raw) }.getOrNull() ?: return null
        val sticker = obj.optString("表情包")
        val url = obj.optString("url")
        return if (sticker.isNotEmpty() && url.isNotEmpty()) url else null
    }

    /** 她的一条输出（当面通道存的是 JSON） */
    class Reply(val speeches: List<String>, val stickerEmotions: List<String>,
               val mood: String = "")

    /** 模型偶尔把"尤夏："也写进台词，显示前切掉（只剥开头一层） */
    private val SPEAKER_PREFIX = Regex("^\\s*(?:尤夏|青梅|她|我|对方|旁白)\\s*[:：]\\s*")

    // 心情 → 头像颜文字（和服务端 config.MOOD_FACES 一致）
    private val MOOD_FACES = mapOf(
        "平静" to "(ᗜ ‸ ᗜ)", "开心" to "(ᗜ ᴗ ᗜ)", "认真" to "(ᗜ ・ ᗜ)", "害羞" to "(ᗜ ﹏ ᗜ)",
        "放松" to "(ᗜ ω ᗜ)", "无语" to "(ᗜ _ ᗜ)", "惊讶" to "(ᗜ o ᗜ)", "不爽" to "(● ‸ ●)",
    )

    fun faceOf(mood: String?): String = MOOD_FACES[(mood ?: "").trim()] ?: ""

    fun stripSpeaker(text: String?): String =
        (text ?: "").replace(SPEAKER_PREFIX, "").trim()

    fun parseReply(content: String): Reply {
        val raw = content.trim()
        if (!raw.startsWith("{")) {
            return Reply(listOf(stripSpeaker(raw)).filter { it.isNotEmpty() }, emptyList())
        }
        val obj = runCatching { JSONObject(raw) }.getOrNull()
            ?: return Reply(listOf(stripSpeaker(raw)), emptyList())
        val speech = obj.optString("对白")
            .split("\n").map { stripSpeaker(it) }.filter { it.isNotEmpty() }
        val stickers = obj.optString("表情包")
            .split(",", "，").map { it.trim() }.filter { it.isNotEmpty() }
        return Reply(speech, stickers)
    }

    /** 按括号配对，从流式文本里取出已经完整的 JSON 对象 */
    fun extractJsonObjects(text: String): List<String> {
        val out = ArrayList<String>()
        var depth = 0
        var start = -1
        var inString = false
        var escaped = false
        text.forEachIndexed { index, ch ->
            if (inString) {
                when {
                    escaped -> escaped = false
                    ch == '\\' -> escaped = true
                    ch == '"' -> inString = false
                }
                return@forEachIndexed
            }
            when (ch) {
                '"' -> inString = true
                '{' -> {
                    if (depth == 0) start = index
                    depth++
                }
                '}' -> {
                    depth--
                    if (depth == 0 && start >= 0) {
                        out.add(text.substring(start, index + 1))
                        start = -1
                    }
                }
            }
        }
        return out
    }

    fun conversations(array: JSONArray): List<ConvBrief> {
        val out = ArrayList<ConvBrief>()
        for (i in 0 until array.length()) {
            val item = array.optJSONObject(i) ?: continue
            val id = item.optString("id")
            if (id.isEmpty()) continue
            out.add(
                ConvBrief(
                    id = id,
                    title = item.optString("title").ifBlank { "和尤夏的故事" },
                    updatedAt = item.optString("updated_at").ifBlank { item.optString("created_at") },
                )
            )
        }
        return out
    }

    fun stickers(obj: JSONObject): List<StickerItem> {
        val items = obj.optJSONArray("items") ?: return emptyList()
        val out = ArrayList<StickerItem>()
        for (i in 0 until items.length()) {
            val item = items.optJSONObject(i) ?: continue
            val url = item.optString("url")
            if (url.isEmpty()) continue
            out.add(
                StickerItem(
                    path = item.optString("path"),
                    url = url,
                    emotion = item.optString("emotion"),
                    bucket = item.optString("bucket").ifBlank { item.optString("emotion") },
                    caption = item.optString("caption"),
                )
            )
        }
        return out
    }

    /** 情绪词 → 图片地址（她输出的是情绪词，需要落到具体图片） */
    fun emotionIndex(stickers: List<StickerItem>): Map<String, String> {
        val map = HashMap<String, String>()
        for (s in stickers) {
            if (s.emotion.isNotEmpty() && !map.containsKey(s.emotion)) map[s.emotion] = s.url
        }
        return map
    }

    /** /api/events 或 /api/history 里的事件数组 */
    fun events(obj: JSONObject): List<EventItem> {
        val arr = obj.optJSONArray("events") ?: return emptyList()
        val out = ArrayList<EventItem>()
        for (i in 0 until arr.length()) {
            val e = arr.optJSONObject(i) ?: continue
            out.add(
                EventItem(
                    id = e.optLong("id"),
                    type = e.optString("event_type").ifBlank { "misc" },
                    title = e.optString("title"),
                    desc = e.optString("description"),
                    status = e.optString("status").ifBlank { "active" },
                    storyTime = e.optString("story_time"),
                )
            )
        }
        return out
    }

    /** 按"第几天"分组（保持时间正序） */
    fun groupByDay(events: List<EventItem>): List<Pair<String, List<EventItem>>> {
        val order = ArrayList<String>()
        val groups = LinkedHashMap<String, MutableList<EventItem>>()
        for (e in events) {
            if (!groups.containsKey(e.day)) {
                groups[e.day] = ArrayList()
                order.add(e.day)
            }
            groups[e.day]!!.add(e)
        }
        return order.map { it to (groups[it] ?: emptyList()) }
    }
}
