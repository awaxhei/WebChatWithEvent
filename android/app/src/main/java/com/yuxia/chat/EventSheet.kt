package com.yuxia.chat

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ExpandLess
import androidx.compose.material.icons.filled.ExpandMore
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateMapOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/** 事件类型对应的小圆点颜色（和网页右侧面板的图标配色一致） */
private fun typeColor(type: String): Color = when (type) {
    "milestone" -> YuxiaColors.Lavender
    "scene_change" -> YuxiaColors.InkFaint
    "emotional_shift" -> YuxiaColors.Sakura
    "time_advance" -> YuxiaColors.InkFaint
    "random" -> YuxiaColors.Peach
    else -> YuxiaColors.LineStrong
}

private val TYPE_LABEL = mapOf(
    "milestone" to "关键进展",
    "scene_change" to "换了地方",
    "emotional_shift" to "情绪变化",
    "time_advance" to "时间流逝",
    "random" to "突发",
    "misc" to "其他",
)

/**
 * 故事进展面板 —— 网页右侧那栏的原生版：
 * 顶部是故事时间，下面是按「第几天」分组的事件卡，最近的默认展开。
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun EventSheet(state: UiState, onClose: () -> Unit, onRefresh: () -> Unit) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    val groups = remember(state.events) { Model.groupByDay(state.events) }
    val collapsed = remember { mutableStateMapOf<String, Boolean>() }
    val latestDay = groups.lastOrNull()?.first

    ModalBottomSheet(onDismissRequest = onClose, sheetState = sheetState) {
        Column(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 320.dp, max = 660.dp)
                .padding(horizontal = 14.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("故事进展", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.width(10.dp))
                Text(
                    state.storyTime,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.weight(1f))
                IconButton(onClick = onRefresh) {
                    Icon(Icons.Filled.Refresh, contentDescription = "刷新")
                }
            }

            // 故事摘要（网页事件栏顶部那行，同一份数据）
            if (state.storySummary.isNotBlank()) {
                Text(
                    state.storySummary,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    maxLines = 3,
                    modifier = Modifier.padding(bottom = 6.dp),
                )
            }

            if (!state.eventsLoaded) {
                Row(
                    Modifier.fillMaxWidth().padding(vertical = 24.dp),
                    verticalAlignment = Alignment.CenterVertically,
                    horizontalArrangement = Arrangement.Center,
                ) {
                    CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(10.dp))
                    Text("正在读取…", style = MaterialTheme.typography.bodySmall)
                }
            } else if (groups.isEmpty()) {
                Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                    Text(
                        "暂无事件记录，开始对话吧",
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                LazyColumn(
                    Modifier.fillMaxWidth().weight(1f, fill = false),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    items(groups) { (day, dayEvents) ->
                        val expanded = collapsed[day] ?: (day == latestDay)
                        Column {
                            Row(
                                Modifier
                                    .fillMaxWidth()
                                    .clickable { collapsed[day] = !expanded }
                                    .padding(vertical = 8.dp),
                                verticalAlignment = Alignment.CenterVertically,
                            ) {
                                Icon(
                                    if (expanded) Icons.Filled.ExpandLess else Icons.Filled.ExpandMore,
                                    contentDescription = null,
                                    modifier = Modifier.size(18.dp),
                                    tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                                Spacer(Modifier.width(6.dp))
                                Text(day, style = MaterialTheme.typography.titleSmall)
                                Spacer(Modifier.width(8.dp))
                                Text(
                                    "${dayEvents.size} 个事件",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                                )
                            }
                            if (expanded) {
                                dayEvents.forEach { ev -> EventCard(ev) }
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
        }
    }
}

@Composable
private fun EventCard(ev: EventItem) {
    Surface(
        color = MaterialTheme.colorScheme.surface,
        shape = RoundedCornerShape(12.dp),
        tonalElevation = 1.dp,
        modifier = Modifier.fillMaxWidth().padding(vertical = 3.dp),
    ) {
        Column(Modifier.padding(horizontal = 12.dp, vertical = 9.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(8.dp)
                        .background(typeColor(ev.type), CircleShape),
                )
                Spacer(Modifier.width(8.dp))
                Text(
                    if (ev.status == "completed") "✓ ${ev.title}" else ev.title,
                    style = MaterialTheme.typography.bodyMedium,
                    fontWeight = FontWeight.SemiBold,
                    color = MaterialTheme.colorScheme.onSurface,
                )
                Spacer(Modifier.weight(1f))
                Text(
                    TYPE_LABEL[ev.type] ?: "",
                    style = MaterialTheme.typography.labelSmall,
                    fontSize = 9.sp,
                    color = typeColor(ev.type),
                )
            }
            if (ev.desc.isNotBlank() && ev.desc != ev.title) {
                Spacer(Modifier.height(4.dp))
                Text(
                    ev.desc,
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            if (ev.clock.isNotBlank()) {
                Spacer(Modifier.height(4.dp))
                Text(
                    ev.clock,
                    style = MaterialTheme.typography.labelSmall,
                    fontSize = 9.sp,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
    }
}
