package com.yuxia.chat

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/**
 * 配色：可爱 / 二次元 / 淡色 / 明亮 / 低饱和 / 干净
 *
 * 思路是「浅粉底 + 灰紫字」，不是「到处粉红」：
 * 粉色只做底色和点缀（气泡、卡片、选中态），文字一律用低饱和暖灰，浅底上才读得清；
 * 主色选灰紫而不是亮粉，因为按钮上是白字，需要对比度。
 */
object YuxiaColors {
    // 底色：比纯白暖一点，整屏偏柔
    val Background = Color(0xFFFFF8FA)     // 极浅樱粉白
    val Surface = Color(0xFFFFFFFF)
    val SurfaceSoft = Color(0xFFFDF1F5)    // 浅粉卡片底
    val SurfaceTint = Color(0xFFF7F2FB)    // 浅紫，用来分第二层

    // 主色 / 点缀：低饱和的灰紫与樱粉
    val Lavender = Color(0xFF8C7BB5)       // 主色（按钮、图标、我这边的高亮）
    val LavenderSoft = Color(0xFFEDE6F7)
    val Sakura = Color(0xFFF2A3B8)         // 樱粉
    val SakuraSoft = Color(0xFFFFE4EC)
    val Mint = Color(0xFF9FD4C2)           // 薄荷
    val Butter = Color(0xFFF6E0B0)         // 奶油黄

    // 文字：低饱和暖灰，不用纯黑
    val Ink = Color(0xFF4E4550)
    val InkSoft = Color(0xFF7A6E80)
    val InkFaint = Color(0xFFA99CAC)       // 旁白、提示

    // 线条与阴影
    val Line = Color(0xFFF3E7ED)           // 分隔线
    val LineStrong = Color(0xFFE7D6DF)     // 卡片描边
    val Shadow = Color(0x1F8C7BB5)         // 带一点紫的柔和阴影

    // 语义色（同样压低）
    val Error = Color(0xFFD98A94)
    val ErrorSoft = Color(0xFFFDECEE)
    val Peach = Color(0xFFF5BFAA)          // 暖色事件标记
}

private val Light = lightColorScheme(
    primary = YuxiaColors.Lavender,
    onPrimary = Color.White,
    primaryContainer = YuxiaColors.LavenderSoft,
    onPrimaryContainer = Color(0xFF4A3F63),
    secondary = YuxiaColors.Sakura,
    onSecondary = Color(0xFF6B3B49),
    secondaryContainer = YuxiaColors.SakuraSoft,
    onSecondaryContainer = Color(0xFF7A4353),
    tertiary = YuxiaColors.Mint,
    onTertiary = Color(0xFF2F5B4E),
    background = YuxiaColors.Background,
    onBackground = YuxiaColors.Ink,
    surface = YuxiaColors.Surface,
    onSurface = YuxiaColors.Ink,
    surfaceVariant = YuxiaColors.SurfaceSoft,
    onSurfaceVariant = YuxiaColors.InkSoft,
    surfaceTint = YuxiaColors.Lavender,
    outline = YuxiaColors.LineStrong,
    outlineVariant = YuxiaColors.Line,
    error = YuxiaColors.Error,
    onError = Color.White,
    errorContainer = YuxiaColors.ErrorSoft,
    onErrorContainer = Color(0xFF6E3B43),
    inverseSurface = Color(0xFF5B5060),
    inverseOnSurface = Color(0xFFFFF8FA),
    scrim = Color(0x336B5F72),
)

// 这个应用只有一套配色：浅粉底 + 灰紫字。
//
// 之前按系统深色模式切了一套深色 scheme，但页面里的底色、气泡、卡片是直接写死的浅色，
// 只有 Material 组件跟着深色走，于是深色手机上会出现一块块全黑的地方。
// 与其把几十处写死的浅色都改成可切换的，不如认定「这就是浅色 app」：
// 系统开深色也不跟随，整屏始终是同一套颜色。
@Composable
fun YuxiaTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = Light,
        content = content,
    )
}
