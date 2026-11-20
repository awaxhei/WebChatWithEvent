package com.yuxia.chat

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// 配色尽量贴近网页版（靛紫 #6366f1 + 浅灰底 + 白卡片）
private val Light = lightColorScheme(
    primary = Color(0xFF6366F1),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE0E7FF),
    onPrimaryContainer = Color(0xFF1E1B4B),
    secondary = Color(0xFF8B5CF6),
    background = Color(0xFFF6F5FB),
    surface = Color.White,
    surfaceVariant = Color(0xFFEFEDF7),
    onSurface = Color(0xFF1A202C),
    onSurfaceVariant = Color(0xFF4A5568),
    outline = Color(0xFF9CA3AF),
    error = Color(0xFFDC2626),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFA5B4FC),
    onPrimary = Color(0xFF1E1B4B),
    primaryContainer = Color(0xFF3730A3),
    onPrimaryContainer = Color(0xFFE0E7FF),
    secondary = Color(0xFFC4B5FD),
    background = Color(0xFF13111C),
    surface = Color(0xFF1C1A26),
    surfaceVariant = Color(0xFF2A2733),
    onSurface = Color(0xFFEDEAF5),
    onSurfaceVariant = Color(0xFFB8B4C6),
    outline = Color(0xFF6B7280),
)

@Composable
fun YuxiaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) Dark else Light,
        content = content,
    )
}
