package com.yuxia.chat

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.ArrowBack
import androidx.compose.material.icons.filled.Schedule
import androidx.compose.material.icons.filled.ListAlt
import androidx.compose.material.icons.filled.AutoAwesome
import androidx.compose.material.icons.filled.EmojiEmotions
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material.icons.filled.Pause
import androidx.compose.material.icons.filled.PhoneIphone
import androidx.compose.material.icons.filled.PhotoLibrary
import androidx.compose.material.icons.filled.Place
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Send
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.AssistChip
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalSoftwareKeyboardController
import androidx.compose.ui.text.font.FontStyle
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import coil.compose.AsyncImage
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun ChatScreen(vm: ChatViewModel) {
    val s = vm.state
    var input by remember { mutableStateOf("") }
    var showPhone by remember { mutableStateOf(false) }
    var showStickers by remember { mutableStateOf(false) }
    var showLocations by remember { mutableStateOf(false) }
    var showEvents by remember { mutableStateOf(false) }
    var showTimeDial by remember { mutableStateOf(false) }
    var menuOpen by remember { mutableStateOf(false) }
    val listState = rememberLazyListState()
    val keyboard = LocalSoftwareKeyboardController.current

    val pickImages = rememberLauncherForActivityResult(
        ActivityResultContracts.PickMultipleVisualMedia(
            // 系统相册一次最多能选多少张有上限，取小的那个免得抛异常
            remember {
                if (android.os.Build.VERSION.SDK_INT >= 33) {
                    minOf(60, android.provider.MediaStore.getPickImagesMaxLimit())
                } else {
                    60
                }
            }
        )
    ) { uris -> vm.importStickers(uris) }

    // 她在页面上的自主行动：每 20 秒问一次（服务端按等待期决定要不要真的动）
    LaunchedEffect(s.convId, s.paused) {
        while (true) {
            delay(20_000)
            vm.pollIdle()
        }
    }
    LaunchedEffect(s.entries.size, s.busy) {
        if (s.entries.isNotEmpty()) {
            runCatching { listState.animateScrollToItem(s.entries.size - 1) }
        }
    }

    Box(Modifier.fillMaxSize()) {
    Scaffold(
        topBar = {
            TopAppBar(
                navigationIcon = {
                    IconButton(onClick = vm::backToConversations) {
                        Icon(Icons.Filled.ArrowBack, contentDescription = "返回")
                    }
                },
                title = {
                    Column {
                        Text("尤夏", style = MaterialTheme.typography.titleMedium)
                        Text(
                            if (s.coLocated) s.location else "${s.location}（你在${s.playerLocation}）",
                            style = MaterialTheme.typography.labelSmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                },
                actions = {
                    IconButton(onClick = { showTimeDial = true }) {
                        Icon(Icons.Filled.Schedule, contentDescription = "调整时间")
                    }
                    IconButton(onClick = { showEvents = true; vm.loadEvents() }) {
                        Icon(Icons.Filled.ListAlt, contentDescription = "故事进展")
                    }
                    IconButton(onClick = { showLocations = true }) {
                        Icon(Icons.Filled.Place, contentDescription = "换地方")
                    }
                    IconButton(onClick = vm::togglePause) {
                        Icon(
                            if (s.paused) Icons.Filled.PlayArrow else Icons.Filled.Pause,
                            contentDescription = if (s.paused) "继续" else "暂停",
                        )
                    }
                    IconButton(onClick = { showPhone = true; vm.loadPhone() }) {
                        BadgedBox(badge = {
                            if (s.unread > 0) Badge { Text("${s.unread}") }
                        }) {
                            Icon(Icons.Filled.PhoneIphone, contentDescription = "手机")
                        }
                    }
                    Box {
                        IconButton(onClick = { menuOpen = true }) {
                            Icon(Icons.Filled.MoreVert, contentDescription = "更多")
                        }
                        DropdownMenu(expanded = menuOpen, onDismissRequest = { menuOpen = false }) {
                            DropdownMenuItem(
                                text = { Text("刷新场景") },
                                onClick = { menuOpen = false; vm.refreshScene() },
                            )
                            DropdownMenuItem(
                                text = { Text("故事推进模式：" + if (s.pushMode) "开" else "关") },
                                onClick = { menuOpen = false; vm.togglePushMode() },
                            )
                            DropdownMenuItem(
                                text = { Text("表情包库") },
                                onClick = { menuOpen = false; showStickers = true; vm.refreshStickers() },
                            )
                            DropdownMenuItem(
                                text = { Text("API Key") },
                                onClick = { menuOpen = false; vm.openApiKeyScreen() },
                            )
                        }
                    }
                },
            )
        },
    ) { padding ->
        Column(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .imePadding(),
        ) {
            SceneStrip(s)

            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                contentPadding = PaddingValues(horizontal = 12.dp, vertical = 10.dp),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(s.entries) { entry -> EntryRow(entry, s.baseUrl) }
                if (s.busy) item { TypingRow() }
            }

            InputBar(
                value = input,
                onValueChange = { input = it },
                busy = s.busy,
                coLocated = s.coLocated,
                pushMode = s.pushMode,
                onTogglePush = vm::togglePushMode,
                onSend = {
                    val text = input
                    input = ""
                    keyboard?.hide()
                    if (s.pushMode) vm.sendPush(text) else vm.send(text)
                },
                onStickers = { showStickers = true; vm.refreshStickers() },
                onPhone = { showPhone = true; vm.loadPhone() },
            )
        }
    }

    if (showEvents) {
        EventSheet(
            state = s,
            onClose = { showEvents = false },
            onRefresh = { vm.loadEvents() },
        )
    }

    if (showTimeDial) {
        TimeDialDialog(
            state = s,
            onClose = { showTimeDial = false },
            onConfirm = { hour, minute -> vm.setTime(hour, minute) },
        )
    }

    if (showPhone) {
        PhoneSheet(
            state = s,
            onClose = { showPhone = false },
            onSend = { text -> vm.send(text, viaPhone = true) },
        )
    }

    if (showStickers) {
        StickerSheet(
            state = s,
            onClose = { showStickers = false },
            onImport = { pickImages.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
            onRefresh = { vm.refreshStickers() },
            onPick = { item, viaPhone ->
                showStickers = false
                vm.sendSticker(item, viaPhone)
            },
        )
    }

    if (showLocations) {
        LocationDialog(
            state = s,
            onClose = { showLocations = false },
            onMove = { name, together -> showLocations = false; vm.moveTo(name, together) },
        )
    }

    s.toast?.let { text ->
        LaunchedEffect(text) {
            delay(2600)
            vm.clearToast()
        }
        Surface(
            color = MaterialTheme.colorScheme.inverseSurface,
            shape = RoundedCornerShape(12.dp),
            modifier = Modifier
                .align(Alignment.BottomCenter)
                .padding(horizontal = 16.dp, vertical = 90.dp),
        ) {
            Text(
                text,
                color = MaterialTheme.colorScheme.inverseOnSurface,
                modifier = Modifier.padding(12.dp),
                style = MaterialTheme.typography.bodySmall,
            )
        }
    }
    }
}

@Composable
private fun SceneStrip(s: UiState) {
    if (s.sceneDesc.isBlank() && s.storyTime.isBlank()) return
    Column(
        Modifier
            .fillMaxWidth()
            .background(MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.45f))
            .padding(horizontal = 14.dp, vertical = 8.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            if (s.storyTime.isNotBlank()) {
                Text(
                    s.storyTime,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            // 现场有谁（煎饼摊老板这类），和网页的场景卡一致
            if (s.npcs.isNotEmpty()) {
                Spacer(Modifier.width(10.dp))
                Text(
                    "在场：" + s.npcs.joinToString("、"),
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.secondary,
                )
            }
        }
        if (s.sceneDesc.isNotBlank()) {
            Spacer(Modifier.height(2.dp))
            Text(
                s.sceneDesc,
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
                maxLines = 2,
            )
        }
    }
}

@Composable
private fun EntryRow(entry: Entry, baseUrl: String) {
    when (entry) {
        is Entry.Me -> Row(
            Modifier.fillMaxWidth(),
            horizontalArrangement = Arrangement.End,
        ) {
            if (entry.stickerUrl.isNotBlank()) {
                StickerImage(baseUrl, entry.stickerUrl)
            } else {
                Bubble(entry.text, mine = true)
            }
        }

        is Entry.Her -> Row(Modifier.fillMaxWidth()) {
            Column {
                if (entry.phone) {
                    Text(
                        "手机",
                        style = MaterialTheme.typography.labelSmall,
                        color = MaterialTheme.colorScheme.primary,
                    )
                }
                Bubble(entry.text, mine = false)
            }
        }

        is Entry.HerSticker -> Row(Modifier.fillMaxWidth()) {
            StickerImage(baseUrl, entry.url)
        }

        is Entry.Narration -> Text(
            entry.text,
            modifier = Modifier.fillMaxWidth(),
            textAlign = TextAlign.Center,
            style = MaterialTheme.typography.labelMedium,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        is Entry.StateDesc -> Text(
            entry.text,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 8.dp),
            style = MaterialTheme.typography.bodySmall,
            fontStyle = FontStyle.Italic,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )

        is Entry.Npc -> Row(Modifier.fillMaxWidth()) {
            Column {
                Text(
                    entry.name,
                    style = MaterialTheme.typography.labelSmall,
                    color = MaterialTheme.colorScheme.secondary,
                )
                Bubble(entry.text, mine = false, dim = true)
            }
        }
    }
}

@Composable
private fun Bubble(text: String, mine: Boolean, dim: Boolean = false) {
    Surface(
        color = when {
            mine -> MaterialTheme.colorScheme.primaryContainer
            dim -> MaterialTheme.colorScheme.surfaceVariant.copy(alpha = 0.6f)
            else -> MaterialTheme.colorScheme.surface
        },
        shape = RoundedCornerShape(
            topStart = 14.dp, topEnd = 14.dp,
            bottomStart = if (mine) 14.dp else 4.dp,
            bottomEnd = if (mine) 4.dp else 14.dp,
        ),
        tonalElevation = 1.dp,
    ) {
        Text(
            text,
            modifier = Modifier
                .widthIn(max = 280.dp)
                .padding(horizontal = 12.dp, vertical = 8.dp),
            style = MaterialTheme.typography.bodyLarge,
        )
    }
}

@Composable
private fun StickerImage(baseUrl: String, url: String) {
    AsyncImage(
        model = Api.abs(baseUrl, url),
        contentDescription = "表情包",
        contentScale = ContentScale.Fit,
        modifier = Modifier
            .size(132.dp)
            .clip(RoundedCornerShape(12.dp)),
    )
}

@Composable
private fun TypingRow() {
    Row(verticalAlignment = Alignment.CenterVertically) {
        CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
        Spacer(Modifier.width(8.dp))
        Text(
            "尤夏在想…",
            style = MaterialTheme.typography.labelSmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
        )
    }
}

@Composable
private fun InputBar(
    value: String,
    onValueChange: (String) -> Unit,
    busy: Boolean,
    coLocated: Boolean,
    pushMode: Boolean,
    onTogglePush: () -> Unit,
    onSend: () -> Unit,
    onStickers: () -> Unit,
    onPhone: () -> Unit,
) {
    Surface(tonalElevation = 2.dp) {
        Row(
            Modifier
                .fillMaxWidth()
                .navigationBarsPadding()
                .padding(horizontal = 8.dp, vertical = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            IconButton(onClick = onStickers) {
                Icon(Icons.Filled.EmojiEmotions, contentDescription = "表情包")
            }
            // 故事推进模式：不扮演玩家说话，只推一把剧情
            IconButton(onClick = onTogglePush) {
                Icon(
                    Icons.Filled.AutoAwesome,
                    contentDescription = "故事推进模式",
                    tint = if (pushMode) MaterialTheme.colorScheme.primary
                    else MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
            OutlinedTextField(
                value = value,
                onValueChange = onValueChange,
                modifier = Modifier.weight(1f),
                placeholder = {
                    Text(
                        when {
                            pushMode -> "推进剧情…（留空＝轻戳一下）"
                            coLocated -> "跟尤夏说点什么…"
                            else -> "不在同一个地方，发消息给她…"
                        }
                    )
                },
                maxLines = 4,
                keyboardOptions = KeyboardOptions(),
                keyboardActions = KeyboardActions(onSend = { if (!busy) onSend() }),
            )
            IconButton(onClick = onPhone) {
                Icon(Icons.Filled.PhoneIphone, contentDescription = "手机通道")
            }
            IconButton(onClick = onSend, enabled = !busy) {
                Icon(Icons.Filled.Send, contentDescription = "发送")
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun PhoneSheet(state: UiState, onClose: () -> Unit, onSend: (String) -> Unit) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var text by remember { mutableStateOf("") }
    val listState = rememberLazyListState()
    LaunchedEffect(state.phoneEntries.size) {
        if (state.phoneEntries.isNotEmpty()) {
            runCatching { listState.animateScrollToItem(state.phoneEntries.size - 1) }
        }
    }

    ModalBottomSheet(onDismissRequest = onClose, sheetState = sheetState) {
        Column(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 320.dp, max = 620.dp)
                .padding(horizontal = 12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Filled.PhoneIphone, contentDescription = null)
                Spacer(Modifier.width(8.dp))
                Text("手机 · 与尤夏", style = MaterialTheme.typography.titleMedium)
            }
            Spacer(Modifier.height(8.dp))
            LazyColumn(
                state = listState,
                modifier = Modifier
                    .weight(1f)
                    .fillMaxWidth(),
                verticalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                items(state.phoneEntries) { entry -> EntryRow(entry, state.baseUrl) }
                if (state.phoneEntries.isEmpty()) {
                    item {
                        Text(
                            "还没有手机消息",
                            style = MaterialTheme.typography.bodySmall,
                            color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
            }
            Spacer(Modifier.height(8.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                OutlinedTextField(
                    value = text,
                    onValueChange = { text = it },
                    modifier = Modifier.weight(1f),
                    placeholder = { Text("发消息…") },
                    maxLines = 3,
                )
                Spacer(Modifier.width(6.dp))
                IconButton(
                    onClick = {
                        val body = text
                        text = ""
                        if (body.isNotBlank()) onSend(body)
                    },
                    enabled = text.isNotBlank(),
                ) {
                    Icon(Icons.Filled.Send, contentDescription = "发送")
                }
            }
            Spacer(Modifier.height(10.dp))
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun StickerSheet(
    state: UiState,
    onClose: () -> Unit,
    onImport: () -> Unit,
    onRefresh: () -> Unit,
    onPick: (StickerItem, Boolean) -> Unit,
) {
    val sheetState = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    var viaPhone by remember { mutableStateOf(!state.coLocated) }

    ModalBottomSheet(onDismissRequest = onClose, sheetState = sheetState) {
        Column(
            Modifier
                .fillMaxWidth()
                .heightIn(min = 320.dp, max = 640.dp)
                .padding(horizontal = 12.dp),
        ) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Text("表情包", style = MaterialTheme.typography.titleMedium)
                Spacer(Modifier.weight(1f))
                TextButton(onClick = onImport) {
                    Icon(Icons.Filled.PhotoLibrary, contentDescription = null)
                    Spacer(Modifier.width(4.dp))
                    Text("相册导入")
                }
                IconButton(onClick = onRefresh) {
                    Icon(Icons.Filled.Refresh, contentDescription = "刷新")
                }
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                FilterChip(selected = !viaPhone, onClick = { viaPhone = false }, label = { Text("当面") })
                FilterChip(selected = viaPhone, onClick = { viaPhone = true }, label = { Text("手机") })
            }
            Spacer(Modifier.height(6.dp))
            if (state.importing) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    CircularProgressIndicator(Modifier.size(16.dp), strokeWidth = 2.dp)
                    Spacer(Modifier.width(8.dp))
                    Text("正在导入…", style = MaterialTheme.typography.bodySmall)
                }
            }
            if (state.stickers.isEmpty()) {
                Box(Modifier.fillMaxWidth().height(160.dp), contentAlignment = Alignment.Center) {
                    Text(
                        "表情包库是空的\n点「相册导入」一次选多张图就会自动入库并打标",
                        textAlign = TextAlign.Center,
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                    )
                }
            } else {
                LazyVerticalGrid(
                    columns = GridCells.Fixed(4),
                    modifier = Modifier.fillMaxWidth().weight(1f),
                    contentPadding = PaddingValues(vertical = 6.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                    horizontalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    items(state.stickers) { item ->
                        Column(
                            horizontalAlignment = Alignment.CenterHorizontally,
                            modifier = Modifier.clickable { onPick(item, viaPhone) },
                        ) {
                            AsyncImage(
                                model = Api.abs(state.baseUrl, item.url),
                                contentDescription = item.emotion,
                                contentScale = ContentScale.Fit,
                                modifier = Modifier
                                    .size(72.dp)
                                    .clip(RoundedCornerShape(10.dp)),
                            )
                            Text(
                                item.emotion.ifBlank { "表情" },
                                style = MaterialTheme.typography.labelSmall,
                                fontSize = 9.sp,
                                color = MaterialTheme.colorScheme.onSurfaceVariant,
                                maxLines = 1,
                            )
                        }
                    }
                }
            }
            Spacer(Modifier.height(10.dp))
        }
    }
}

@Composable
private fun LocationDialog(
    state: UiState,
    onClose: () -> Unit,
    onMove: (String, Boolean) -> Unit,
) {
    var together by remember { mutableStateOf(false) }
    AlertDialog(
        onDismissRequest = onClose,
        confirmButton = {
            TextButton(onClick = onClose) { Text("取消") }
        },
        title = { Text("去哪儿") },
        text = {
            Column {
                if (state.coLocated) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        AssistChip(
                            onClick = { together = !together },
                            label = { Text(if (together) "带尤夏一起 ✓" else "带尤夏一起") },
                        )
                    }
                }
                Spacer(Modifier.height(6.dp))
                val locations = if (state.locations.isEmpty()) listOf("公寓客厅", "厨房", "卧室") else state.locations
                LazyColumn(Modifier.heightIn(max = 360.dp)) {
                    items(locations) { name ->
                        Text(
                            name,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { onMove(name, together && state.coLocated) }
                                .padding(vertical = 12.dp, horizontal = 4.dp),
                        )
                    }
                }
            }
        },
    )
}
