package com.yuxia.chat

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val Light = lightColorScheme(
    primary = Color(0xFF6B5BD2),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFE6E1FB),
    onPrimaryContainer = Color(0xFF221A4D),
    secondary = Color(0xFF6E7A8A),
    background = Color(0xFFF6F5FB),
    surface = Color.White,
    surfaceVariant = Color(0xFFEFEDF7),
    onSurface = Color(0xFF1B1A22),
    onSurfaceVariant = Color(0xFF4A4757),
    error = Color(0xFFB3261E),
)

private val Dark = darkColorScheme(
    primary = Color(0xFFB7A9FF),
    onPrimary = Color(0xFF241B55),
    primaryContainer = Color(0xFF3B2F73),
    onPrimaryContainer = Color(0xFFE8E2FF),
    background = Color(0xFF14131A),
    surface = Color(0xFF1D1B24),
    surfaceVariant = Color(0xFF2A2733),
    onSurface = Color(0xFFE7E4EF),
    onSurfaceVariant = Color(0xFFC8C4D4),
)

@Composable
fun YuxiaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = if (isSystemInDarkTheme()) Dark else Light,
        content = content,
    )
}
