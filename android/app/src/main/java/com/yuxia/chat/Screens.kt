package com.yuxia.chat

import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.ChatBubbleOutline
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.DeleteOutline
import androidx.compose.material.icons.filled.Logout
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.ExtendedFloatingActionButton
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import kotlinx.coroutines.delay
import androidx.compose.runtime.DisposableEffect
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver

@Composable
fun AppRoot(vm: ChatViewModel) {
    val s = vm.state
    // 回到前台就对齐一次：后台时她可能已经发过消息（通知先到），界面得跟上
    val lifecycle = LocalLifecycleOwner.current
    DisposableEffect(lifecycle) {
        val observer = LifecycleEventObserver { _, event ->
            if (event == Lifecycle.Event.ON_RESUME) vm.reloadOnResume()
        }
        lifecycle.lifecycle.addObserver(observer)
        onDispose { lifecycle.lifecycle.removeObserver(observer) }
    }
    when (s.screen) {
        Screen.Boot -> BootScreen(s, onRetry = vm::retryBoot)
        Screen.ApiKey -> ApiKeyScreen(s, onSave = vm::saveApiKey, onSkip = { vm.enterHomeFromKeyScreen() })
        Screen.Conversations -> ConversationsScreen(
            state = s,
            onOpen = vm::openConversation,
            onNew = vm::newConversation,
            onRefresh = { vm.loadConversations() },
            onApiKey = vm::openApiKeyScreen,
            onDelete = vm::deleteConversation,
            onDeleteMany = vm::deleteConversations,
        )
        Screen.Chat -> ChatScreen(vm)
    }
}

@Composable
fun BootScreen(state: UiState, onRetry: () -> Unit) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally, modifier = Modifier.padding(28.dp)) {
            if (state.error.isNullOrBlank()) {
                var seconds by remember { mutableStateOf(0) }
                LaunchedEffect(Unit) {
                    while (true) {
                        delay(1000)
                        seconds += 1
                    }
                }
                CircularProgressIndicator(Modifier.size(28.dp))
                Spacer(Modifier.height(14.dp))
                Text(
                    "正在初始化本机服务…" + if (seconds > 0) "（${seconds} 秒）" else "",
                    style = MaterialTheme.typography.bodyMedium,
                )
                Spacer(Modifier.height(6.dp))
                Text(
                    "首次启动需要解压运行环境，耗时相对较长；此后启动将明显加快。",
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                    textAlign = TextAlign.Center,
                )
            } else {
                Text(
                    state.error,
                    color = MaterialTheme.colorScheme.error,
                    style = MaterialTheme.typography.bodyMedium,
                    textAlign = TextAlign.Center,
                )
                Spacer(Modifier.height(16.dp))
                Button(onClick = onRetry) { Text("重新启动") }
            }
        }
    }
}

@Composable
fun LoadingScreen(text: String) {
    Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
        Column(horizontalAlignment = Alignment.CenterHorizontally) {
            CircularProgressIndicator(Modifier.size(28.dp))
            Spacer(Modifier.height(12.dp))
            Text(text, style = MaterialTheme.typography.bodyMedium)
        }
    }
}

/** 第一次使用：填自己的 DeepSeek API Key（存在手机里，不进 APK） */
@Composable
fun ApiKeyScreen(state: UiState, onSave: (String) -> Unit, onSkip: () -> Unit) {
    var key by remember { mutableStateOf(state.apiKey) }

    Column(
        modifier = Modifier
            .fillMaxSize()
            .padding(28.dp),
        verticalArrangement = Arrangement.Center,
    ) {
        Icon(
            Icons.Filled.ChatBubbleOutline, contentDescription = null,
            modifier = Modifier.size(40.dp), tint = MaterialTheme.colorScheme.primary,
        )
        Spacer(Modifier.height(14.dp))
        Text("尤夏", style = MaterialTheme.typography.headlineMedium)
        Spacer(Modifier.height(6.dp))
        Text(
            "全部服务均在本机运行，无需电脑或远程服务器。填入 DeepSeek API Key 即可开始。",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
        Spacer(Modifier.height(22.dp))

        OutlinedTextField(
            value = key, onValueChange = { key = it },
            label = { Text("DeepSeek API Key") },
            placeholder = { Text("sk-…") },
            singleLine = true,
            visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth(),
        )

        if (!state.error.isNullOrBlank()) {
            Spacer(Modifier.height(10.dp))
            Text(
                state.error,
                color = MaterialTheme.colorScheme.error,
                style = MaterialTheme.typography.bodySmall,
            )
        }

        Spacer(Modifier.height(20.dp))
        Button(
            onClick = { onSave(key) },
            enabled = !state.busy,
            modifier = Modifier.fillMaxWidth(),
        ) {
            if (state.busy) {
                CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
            } else {
                Text("保存并开始")
            }
        }
        if (state.apiKey.isNotEmpty()) {
            Spacer(Modifier.height(10.dp))
            TextButton(onClick = onSkip, modifier = Modifier.fillMaxWidth()) { Text("沿用当前 API Key") }
        }
        Spacer(Modifier.height(14.dp))
        Text(
            "API Key 仅保存在本机应用数据中，服务端不存储。如需更换，请在聊天页右上角 ⋯ 中操作。",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@OptIn(ExperimentalMaterial3Api::class, androidx.compose.foundation.ExperimentalFoundationApi::class)
@Composable
fun ConversationsScreen(
    state: UiState,
    onOpen: (String) -> Unit,
    onNew: () -> Unit,
    onRefresh: () -> Unit,
    onApiKey: () -> Unit,
    onDelete: (String) -> Unit,
    onDeleteMany: (List<String>) -> Unit,
) {
    var pendingDelete by remember { mutableStateOf<ConvBrief?>(null) }
    var pendingBulk by remember { mutableStateOf(false) }
    var selecting by remember { mutableStateOf(false) }
    var picked by remember { mutableStateOf(setOf<String>()) }

    fun exitSelect() {
        selecting = false
        picked = emptySet()
    }

    Scaffold(
        topBar = {
            TopAppBar(
                title = {
                    Text(if (selecting) "已选 ${picked.size} 项" else "和尤夏的故事")
                },
                navigationIcon = {
                    if (selecting) {
                        IconButton(onClick = { exitSelect() }) {
                            Icon(Icons.Filled.Close, contentDescription = "取消")
                        }
                    }
                },
                actions = {
                    if (selecting) {
                        TextButton(onClick = { picked = state.convs.map { it.id }.toSet() }) {
                            Text("全选")
                        }
                        IconButton(
                            onClick = { if (picked.isNotEmpty()) pendingBulk = true },
                            enabled = picked.isNotEmpty(),
                        ) {
                            Icon(
                                Icons.Filled.DeleteOutline,
                                contentDescription = "删除所选",
                                tint = if (picked.isEmpty()) MaterialTheme.colorScheme.onSurfaceVariant
                                       else MaterialTheme.colorScheme.error,
                            )
                        }
                    } else {
                        IconButton(onClick = onRefresh) {
                            Icon(Icons.Filled.Refresh, contentDescription = "刷新")
                        }
                        IconButton(onClick = onApiKey) {
                            Icon(Icons.Filled.Settings, contentDescription = "API Key")
                        }
                    }
                },
            )
        },
        floatingActionButton = {
            if (!selecting) {
                ExtendedFloatingActionButton(
                    onClick = onNew,
                    icon = { Icon(Icons.Filled.Add, contentDescription = null) },
                    text = { Text("新对话") },
                )
            }
        },
    ) { padding ->
        if (state.convs.isEmpty()) {
            Box(Modifier.padding(padding).fillMaxSize(), contentAlignment = Alignment.Center) {
                Text(
                    if (state.busy) "正在读取…" else "暂无会话。点击右下角新建会话。",
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        } else {
            LazyColumn(
                modifier = Modifier.padding(padding).fillMaxSize(),
                contentPadding = PaddingValues(bottom = 96.dp),
            ) {
                items(state.convs) { conv ->
                    val checked = conv.id in picked
                    ListItem(
                        headlineContent = { Text(conv.title) },
                        supportingContent = {
                            Text(conv.updatedAt, style = MaterialTheme.typography.labelSmall)
                        },
                        leadingContent = if (selecting) {
                            {
                                Checkbox(
                                    checked = checked,
                                    onCheckedChange = {
                                        picked = if (checked) picked - conv.id else picked + conv.id
                                    },
                                )
                            }
                        } else null,
                        trailingContent = if (selecting) {
                            null
                        } else {
                            {
                                IconButton(onClick = { pendingDelete = conv }) {
                                    Icon(
                                        Icons.Filled.DeleteOutline,
                                        contentDescription = "删除",
                                        tint = MaterialTheme.colorScheme.onSurfaceVariant,
                                    )
                                }
                            }
                        },
                        // 长按进入多选；多选态下点一下就是勾选
                        modifier = Modifier.combinedClickable(
                            onClick = {
                                if (selecting) {
                                    picked = if (checked) picked - conv.id else picked + conv.id
                                } else {
                                    onOpen(conv.id)
                                }
                            },
                            onLongClick = {
                                if (!selecting) {
                                    selecting = true
                                    picked = setOf(conv.id)
                                }
                            },
                        ),
                    )
                    HorizontalDivider()
                }
            }
        }
    }

    pendingDelete?.let { conv ->
        AlertDialog(
            onDismissRequest = { pendingDelete = null },
            title = { Text("删除对话") },
            text = { Text("将删除「${conv.title}」中的全部消息、事件与记忆，且不可恢复。") },
            confirmButton = {
                TextButton(onClick = {
                    onDelete(conv.id)
                    pendingDelete = null
                }) { Text("删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { pendingDelete = null }) { Text("取消") }
            },
        )
    }

    if (pendingBulk) {
        AlertDialog(
            onDismissRequest = { pendingBulk = false },
            title = { Text("批量删除") },
            text = { Text("确认删除选中的 ${picked.size} 个会话？其中的全部消息、事件与记忆将一并删除，且不可恢复。") },
            confirmButton = {
                TextButton(onClick = {
                    val ids = picked.toList()
                    pendingBulk = false
                    exitSelect()
                    onDeleteMany(ids)
                }) { Text("删除", color = MaterialTheme.colorScheme.error) }
            },
            dismissButton = {
                TextButton(onClick = { pendingBulk = false }) { Text("取消") }
            },
        )
    }
}
