package com.yuxia.chat

import android.Manifest
import android.content.Intent
import android.os.Build
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.viewmodel.compose.viewModel
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

class MainActivity : ComponentActivity() {

    private val askNotification =
        registerForActivityResult(ActivityResultContracts.RequestPermission()) { }

    /** 从通知点进来的会话 id */
    private var openConvId by mutableStateOf("")

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        Notifications.ensureChannels(this)

        val fromIntent = intent?.getStringExtra("conversation_id").orEmpty()
        if (fromIntent.isNotEmpty()) Prefs.setConvId(this, fromIntent)

        if (Build.VERSION.SDK_INT >= 33) {
            runCatching { askNotification.launch(Manifest.permission.POST_NOTIFICATIONS) }
        }

        setContent {
            YuxiaTheme {
                val vm: ChatViewModel = viewModel()

                // 内嵌的 Python 后端（没有电脑、没有远程服务器）
                LaunchedEffect(Unit) {
                    val error = withContext(Dispatchers.IO) {
                        runCatching { Backend.start(applicationContext) }.exceptionOrNull()
                            ?.let { it.message ?: it.javaClass.simpleName }
                            ?: ""
                    }
                    vm.onBackendReady(error.isEmpty(), error)
                }
                LaunchedEffect(openConvId) {
                    if (openConvId.isNotEmpty()) {
                        vm.openConversation(openConvId)
                        openConvId = ""
                    }
                }
                AppRoot(vm)
            }
        }
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        val conv = intent.getStringExtra("conversation_id").orEmpty()
        if (conv.isNotEmpty()) {
            Prefs.setConvId(this, conv)
            openConvId = conv
        }
    }

    override fun onStart() {
        super.onStart()
        // 回到前台：页面自己会轮询，后台服务可以歇着
        NotifyService.stop(this)
    }

    override fun onStop() {
        super.onStop()
        // 退到后台：开常驻轮询，她发消息会弹通知
        if (Prefs.notifyEnabled(this) && Prefs.convId(this).isNotEmpty()) {
            NotifyService.start(this)
        }
    }
}
