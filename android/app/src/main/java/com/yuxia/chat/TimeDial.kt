package com.yuxia.chat

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.gestures.detectDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlin.math.atan2
import kotlin.math.cos
import kotlin.math.roundToInt
import kotlin.math.sin

/**
 * 24 小时时间表盘（和网页一致的"原神式"手感）：
 * 顶端 06:00、右 12:00、下 18:00、左 00:00，只能往前走，服务端最多允许 +3 天。
 */
@Composable
fun TimeDialDialog(
    state: UiState,
    onClose: () -> Unit,
    onConfirm: (hour: Int, minute: Int) -> Unit,
) {
    val nowOfDay = state.storyMinutes % 1440
    val nowDay = state.storyMinutes / 1440 + 1
    // 默认落在"现在 + 1 小时"
    var minutes by remember { mutableStateOf(((nowOfDay / 5 * 5) + 60) % 1440) }

    val dayOffset = if (minutes <= nowOfDay) 1 else 0
    val targetDay = nowDay + dayOffset
    val clock = fmtClock(minutes)
    val primary = MaterialTheme.colorScheme.primary
    val outline = MaterialTheme.colorScheme.outline
    val measurer = rememberTextMeasurer()

    AlertDialog(
        onDismissRequest = onClose,
        confirmButton = {
            TextButton(onClick = {
                onConfirm(minutes / 60, minutes % 60)
                onClose()
            }) { Text("拨到这时候") }
        },
        dismissButton = { TextButton(onClick = onClose) { Text("取消") } },
        title = { Text("调整时间") },
        text = {
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text(
                    "现在 第${nowDay}天 ${fmtClock(nowOfDay)}",
                    style = MaterialTheme.typography.labelMedium,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
                Spacer(Modifier.height(8.dp))

                val sizeDp = 232.dp
                // 指针角度：06:00 在正上方
                val angle = (((minutes - 360) / 1440f) * 360f + 360f) % 360f
                Canvas(
                    Modifier
                        .size(sizeDp)
                        .pointerInput(Unit) {
                            fun update(pos: Offset, w: Float, h: Float) {
                                val dx = pos.x - w / 2f
                                val dy = pos.y - h / 2f
                                if (dx == 0f && dy == 0f) return
                                val deg = ((Math.toDegrees(atan2(dx.toDouble(), -dy.toDouble())) + 360) % 360).toFloat()
                                var m = ((deg / 360f) * 1440f + 360f) % 1440f
                                m = ((m / 5f).roundToInt() * 5 % 1440).toFloat()
                                minutes = m.roundToInt()
                            }
                            detectDragGestures(
                                onDragStart = { pos -> update(pos, size.width.toFloat(), size.height.toFloat()) },
                                onDrag = { change, _ ->
                                    update(change.position, size.width.toFloat(), size.height.toFloat())
                                },
                            )
                        }
                        .pointerInput(Unit) {
                            detectTapGestures { pos ->
                                val dx = pos.x - size.width / 2f
                                val dy = pos.y - size.height / 2f
                                if (dx == 0f && dy == 0f) return@detectTapGestures
                                val deg = ((Math.toDegrees(atan2(dx.toDouble(), -dy.toDouble())) + 360) % 360).toFloat()
                                var m = ((deg / 360f) * 1440f + 360f) % 1440f
                                m = ((m / 5f).roundToInt() * 5 % 1440).toFloat()
                                minutes = m.roundToInt()
                            }
                        },
                ) {
                    val cx = size.width / 2f
                    val cy = size.height / 2f
                    val r = size.minDimension / 2f
                    // 外圈 + 表盘底
                    drawCircle(color = outline.copy(alpha = 0.35f), radius = r - 1f,
                        center = Offset(cx, cy), style = androidx.compose.ui.graphics.drawscope.Stroke(2f))
                    drawCircle(color = primary.copy(alpha = 0.06f), radius = r - 8f, center = Offset(cx, cy))
                    // 24 个刻度，每 6 小时一个带数字的
                    for (i in 0 until 24) {
                        val rad = Math.toRadians(i / 24.0 * 360.0)
                        val major = i % 6 == 0
                        val r1 = r - 10f
                        val r2 = r - if (major) 26f else 18f
                        drawLine(
                            color = if (major) outline else outline.copy(alpha = 0.4f),
                            start = Offset(cx + (r1 * sin(rad)).toFloat(), cy - (r1 * cos(rad)).toFloat()),
                            end = Offset(cx + (r2 * sin(rad)).toFloat(), cy - (r2 * cos(rad)).toFloat()),
                            strokeWidth = if (major) 3f else 1.5f,
                        )
                        if (major) {
                            val lx = cx + (r - 42f) * sin(rad).toFloat()
                            val ly = cy - (r - 42f) * cos(rad).toFloat()
                            val label = measurer.measure(
                                text = String.format("%02d", i),
                                style = androidx.compose.ui.text.TextStyle(
                                    fontSize = 10.sp,
                                    color = outline,
                                ),
                            )
                            drawText(
                                textLayoutResult = label,
                                topLeft = Offset(lx - label.size.width / 2f, ly - label.size.height / 2f),
                            )
                        }
                    }
                    // 指针 + 把手
                    val rad = Math.toRadians(angle.toDouble())
                    val handEnd = Offset(cx + (r - 12f) * sin(rad).toFloat(), cy - (r - 12f) * cos(rad).toFloat())
                    drawLine(color = primary, start = Offset(cx, cy), end = handEnd, strokeWidth = 4f)
                    drawCircle(color = primary, radius = 9f, center = handEnd)
                    drawCircle(color = Color.White, radius = 4f, center = handEnd)
                }

                Spacer(Modifier.height(6.dp))
                Text(
                    "目标：第${targetDay}天 $clock",
                    style = MaterialTheme.typography.titleSmall,
                    color = primary,
                )
                Spacer(Modifier.height(10.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    listOf("清晨" to 6, "正午" to 12, "黄昏" to 18, "深夜" to 22).forEach { (label, h) ->
                        AssistChip(onClick = { minutes = h * 60 }, label = { Text(label) })
                    }
                }
                Spacer(Modifier.height(4.dp))
                Text(
                    "只能往后拨；一次最多三天（服务端限制）",
                    style = MaterialTheme.typography.labelSmall,
                    textAlign = TextAlign.Center,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        },
    )
}

private fun fmtClock(minutes: Int): String {
    val m = ((minutes % 1440) + 1440) % 1440
    return String.format("%02d:%02d", m / 60, m % 60)
}
