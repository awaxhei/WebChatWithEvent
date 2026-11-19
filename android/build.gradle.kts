plugins {
    id("com.android.application") version "8.5.2" apply false
    // Kotlin 用 1.9.x：Chaquopy 17.x 与 Kotlin 2.x 的 friendPaths 解析有冲突
    // （会报 "Current thread does not hold the state lock"）
    id("org.jetbrains.kotlin.android") version "1.9.24" apply false
    id("com.chaquo.python") version "17.2.0" apply false
}
