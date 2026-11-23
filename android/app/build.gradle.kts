plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("com.chaquo.python")
}

android {
    namespace = "com.yuxia.chat"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.yuxia.chat"
        minSdk = 26
        targetSdk = 34
        versionCode = 36
        versionName = "5.4"

        // 只打手机和模拟器需要的 ABI（每个 ABI 都要塞一份 CPython，越少越好）
        // Python 3.12 只支持 64 位，所以不带 armeabi-v7a（Android 8+ 手机基本都是 arm64）
        ndk {
            abiFilters.addAll(listOf("arm64-v8a", "x86_64"))
        }
    }

    buildTypes {
        release {
            // 侧载用：不混淆，免得反射/JSON 解析出问题
            isMinifyEnabled = false
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"), "proguard-rules.pro")
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }

    // Kotlin 1.9 用独立的 Compose 编译器
    composeOptions {
        kotlinCompilerExtensionVersion = "1.5.14"
    }

    packaging {
        resources.excludes += "/META-INF/{AL2.0,LGPL2.1}"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-compose:2.8.6")
    implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.6")

    implementation(platform("androidx.compose:compose-bom:2024.09.03"))
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.ui:ui-tooling-preview")
    implementation("androidx.compose.foundation:foundation")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-extended")

    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("io.coil-kt:coil-compose:2.7.0")

    debugImplementation("androidx.compose.ui:ui-tooling")
}

chaquopy {
    defaultConfig {
        // 内嵌解释器版本（构建机上的 Python 主次版本必须一致）
        version = "3.12"
        // 构建机上的解释器，只用来跑 pip 打包依赖（不会进 APK）
        // build_apk.ps1 会把它设成实际路径
        buildPython(System.getenv("YUXIA_PYTHON") ?: "python")
        pip {
            // 注意：不能把 --index-url 换成国内镜像——Chaquopy 自己托管的
            // 原生包 wheel（markupsafe 等）只在默认索引里，换掉就解析不出来。
            install("flask")
            install("PyJWT")
            install("requests")
        }
    }
}
