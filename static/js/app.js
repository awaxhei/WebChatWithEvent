/**
 * AI 情感助手 - 前端类架构 (优化版)
 * - AI A JSON 格式解析 + 分离渲染（对白/动作/消息/表情包）
 * - 异步氛围/事件 API 调用
 * - 表情包随机选取
 */
class AuthManager {
    constructor() {
        this.overlay = document.getElementById('loginOverlay');
        this.usernameInput = document.getElementById('loginUsername');
        this.passwordInput = document.getElementById('loginPassword');
        this.rememberCheck = document.getElementById('rememberPwd');
        this.btnLogin = document.getElementById('btnLogin');
        this.errorEl = document.getElementById('loginError');
        this.offlineEl = document.getElementById('loginOffline');
        this.retryBtn = document.getElementById('btnRetryConnect');
        this._retryTimer = null;
        this._bindEvents();
    }
    _bindEvents() {
        this.btnLogin.addEventListener('click', () => this._handleLogin());
        this.passwordInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._handleLogin(); });
        this.usernameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.passwordInput.focus(); });
        if (this.retryBtn) this.retryBtn.addEventListener('click', () => this._reconnect());
    }
    async init() {
        if (!await this._ping()) { this._showOverlay(); this._showOffline(); return false; }
        this._hideOffline();
        const valid = await this._verifyToken();
        if (valid) { this._hideOverlay(); return true; }
        this._showOverlay(); return false;
    }
    async _reconnect() {
        const ok = await this.init();
        if (ok) onAuthSuccess();
    }
    // 后端存活探测：区别于"未登录"，只是证明 127.0.0.1:PORT 上的进程还在
    async _ping() {
        try {
            const ctrl = new AbortController();
            const timer = setTimeout(() => ctrl.abort(), 3000);
            const res = await fetch('/api/ping', { cache: 'no-store', signal: ctrl.signal });
            clearTimeout(timer);
            return res.ok;
        } catch { return false; }
    }
    async _verifyToken() {
        try { const res = await fetch('/api/auth/verify', { credentials: 'include' }); return (await res.json()).valid === true; }
        catch { return false; }
    }
    async _handleLogin() {
        const username = this.usernameInput.value.trim();
        const password = this.passwordInput.value;
        const remember = this.rememberCheck.checked;
        if (!username || !password) { this._showError('请输入用户名和密码'); return; }
        this.btnLogin.disabled = true; this.errorEl.textContent = '';
        try {
            const res = await fetch('/api/auth/login', { method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password, remember }) });
            const data = await res.json();
            if (data.success) { this._hideOffline(); this._hideOverlay(); onAuthSuccess(); }
            else { this._showError(data.error || '登录失败'); this.passwordInput.value = ''; this.passwordInput.focus(); }
        } catch { this._showOffline(); }
        this.btnLogin.disabled = false;
    }
    // 后端掉线：明确告知原因，并每 5 秒自动重连（重启 python app.py 后页面自己恢复）
    _showOffline() {
        if (this.offlineEl) this.offlineEl.classList.remove('hidden');
        if (this._retryTimer) return;
        this._retryTimer = setInterval(() => this._reconnect(), 5000);
    }
    _hideOffline() {
        if (this.offlineEl) this.offlineEl.classList.add('hidden');
        if (this._retryTimer) { clearInterval(this._retryTimer); this._retryTimer = null; }
    }
    _showError(msg) { this.errorEl.textContent = msg; this.errorEl.style.animation = 'none'; this.errorEl.offsetHeight; this.errorEl.style.animation = 'fadeIn 0.3s ease-out'; setTimeout(() => { this.errorEl.textContent = ''; }, 3000); }
    _showOverlay() { this.overlay.classList.remove('hidden'); this.usernameInput.focus(); }
    _hideOverlay() { this.overlay.classList.add('hidden'); }
}
const authFetch = (url, options = {}) => {
    const extra = (window.apiKeyManager && window.apiKeyManager.getHeaders()) || {};
    return fetch(url, {
        ...options,
        credentials: 'include',
        headers: { ...(options.headers || {}), ...extra },
    });
};

const SVG_ICONS = {
    milestone: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6366f1" stroke-width="1.5"><polygon points="8 1.5 10 6 15 6 11.2 9 12.5 13.5 8 11 3.5 13.5 4.8 9 1 6 6 6"/></svg>',
    scene_change: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><path d="M3 8h10M8 3l5 5-5 5"/></svg>',
    emotional_shift: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><circle cx="5" cy="7" r="1"/><circle cx="11" cy="7" r="1"/><path d="M3 11c1.5 2 5.5 2 10 0"/></svg>',
    time_advance: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><circle cx="8" cy="8" r="6.5"/><polyline points="8 5 8 8 10.5 10.5"/></svg>',
    random: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#f59e0b" stroke-width="1.5"><rect x="2.5" y="2.5" width="11" height="11" rx="3"/><circle cx="5.5" cy="5.5" r="1.2"/><circle cx="10.5" cy="5.5" r="1.2"/><circle cx="8" cy="9" r="1"/></svg>',
    misc: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#a0aec0" stroke-width="1.5"><circle cx="8" cy="8" r="2"/><path d="M8 1v2m0 10v2M1 8h2m10 0h2"/></svg>',
};

// ── 表情包：导入即用，由后端按情绪语义匹配（无需人工分类） ──
let STICKER_STATS = { total: 0, tagged: 0, emotions: [] };

async function loadStickerStats() {
    try {
        const res = await fetch('/api/stickers/stats');
        STICKER_STATS = await res.json();
    } catch (e) {
        STICKER_STATS = { total: 0, tagged: 0, emotions: [] };
    }
}

class AnimationEngine {
    static slideIn(el, delay = 0) { el.style.animation = 'none'; el.offsetHeight; el.style.animation = `messageSlideIn 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) ${delay}ms both`; }
    static fadeIn(el) { el.style.animation = 'none'; el.offsetHeight; el.style.animation = 'fadeIn 0.4s ease-out both'; }
    static scrollToBottom(container) { container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' }); }
    static showThinking(container) { const row = document.createElement('div'); row.className = 'message-row assistant'; row.id = 'thinkingRow'; const bubble = document.createElement('div'); bubble.className = 'message-bubble thinking-indicator'; for (let i = 0; i < 3; i++) { const dot = document.createElement('span'); dot.className = 'thinking-dot'; bubble.appendChild(dot); } row.appendChild(bubble); container.appendChild(row); row.style.animation = 'messageSlideIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) both'; this.scrollToBottom(container); return row; }
    static hideThinking() { const row = document.getElementById('thinkingRow'); if (row) { row.style.opacity = '0'; row.style.transform = 'scale(0.95)'; row.style.transition = 'opacity 0.2s, transform 0.2s'; setTimeout(() => row.remove(), 200); } }
    static hideWelcome() { const w = document.querySelector('.welcome-message'); if (w) { w.style.opacity = '0'; w.style.transform = 'translateY(10px)'; w.style.transition = 'opacity 0.3s, transform 0.3s'; setTimeout(() => w.remove(), 300); } }
}

class ChatManager {
    constructor() {
        this.currentConvId = ''; this.isProcessing = false; this.pushMode = false;
        this.coLocated = true;
        this.paused = false;
        this.moveTogether = false;
        this.messagesContainer = document.getElementById('chatMessages');
        this.inputEl = document.getElementById('messageInput');
        this.btnSend = document.getElementById('btnSend');
        this.btnPushMode = document.getElementById('btnPushMode');
        this.btnPause = document.getElementById('btnPause');
        this.locationTag = document.getElementById('locationTag');
        this.locationPicker = document.getElementById('locationPicker');
        this.sceneDesc = document.getElementById('sceneDesc');
        this.sceneCard = document.getElementById('sceneCard');
        this.welcomeEl = document.querySelector('.welcome-message');
        this._bindEvents();
    }
    _bindEvents() {
        this.btnSend.addEventListener('click', () => this.sendMessage());
        this.inputEl.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendMessage(); } });
        this.inputEl.addEventListener('input', () => { this.inputEl.style.height = 'auto'; this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 120) + 'px'; });
        if (this.btnPushMode) this.btnPushMode.addEventListener('click', () => this.togglePushMode());
        if (this.btnPause) this.btnPause.addEventListener('click', (e) => {
            e.stopPropagation();
            this.togglePause();
        });
        // 地点按钮：阻止冒泡，否则会连带触发"点击标题切换故事面板"
        if (this.locationTag) this.locationTag.addEventListener('click', (e) => {
            e.stopPropagation();
            this._toggleLocationPicker();
        });
        if (this.locationPicker) {
            this.locationPicker.addEventListener('click', (e) => {
                e.stopPropagation();
                const item = e.target.closest('[data-loc]');
                if (!item) return;
                this.moveTo(item.dataset.loc);
                this._toggleLocationPicker(false);
            });
            this.locationPicker.addEventListener('change', (e) => {
                if (e.target && e.target.id === 'moveTogether') {
                    this.moveTogether = !!e.target.checked;
                }
            });
            this.locationPicker.addEventListener('keydown', (e) => {
                if (e.key === 'Escape') { this._toggleLocationPicker(false); return; }
                if (e.target && e.target.id === 'newLocationInput' && e.key === 'Enter') {
                    e.preventDefault();
                    const name = (e.target.value || '').trim();
                    if (!name) return;
                    e.target.value = '';
                    this.moveTo(name);
                    this._toggleLocationPicker(false);
                }
            });
        }
        // 点空白处收起地点列表
        document.addEventListener('click', () => this._toggleLocationPicker(false));
    }
    togglePushMode() { this.pushMode = !this.pushMode; if (this.pushMode) { this.btnPushMode.classList.add('active'); this.btnSend.classList.add('push-active'); this.inputEl.placeholder = '输入推进方向...'; } else { this.btnPushMode.classList.remove('active'); this.btnSend.classList.remove('push-active'); this._updatePlaceholder(); } }

    // ── 暂停自动推进（她不自主行动、世界引擎停转；正常对话不受影响） ──
    async togglePause() {
        this._setPaused(!this.paused);
        const id = this.currentConvId;
        if (!id) return;
        try {
            await authFetch(`/api/pause/${id}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ paused: this.paused }),
            });
        } catch (e) { /* 本地状态已切换，下一个场景刷新会对齐 */ }
    }
    _setPaused(paused) {
        this.paused = !!paused;
        if (this.btnPause) {
            this.btnPause.classList.toggle('paused', this.paused);
            this.btnPause.title = this.paused ? '已暂停：她不会自己行动，点此继续' : '暂停自动推进';
            this.btnPause.setAttribute('aria-pressed', this.paused ? 'true' : 'false');
        }
        if (window.emotionAssistant && window.emotionAssistant._startIdlePolling) {
            window.emotionAssistant._startIdlePolling();
        }
    }

    _updatePlaceholder() {
        this.inputEl.placeholder = this.coLocated ? '跟尤夏说点什么...' : '不在同一个地方，发消息给她...';
    }
    _disableInput() { this.inputEl.disabled = true; this.btnSend.disabled = true; if (this.btnPushMode) this.btnPushMode.disabled = true; }
    _enableInput() { this.inputEl.disabled = false; this.btnSend.disabled = false; if (this.btnPushMode) this.btnPushMode.disabled = false; this.inputEl.focus(); }

    // ── 场景 ──
    async loadScene() {
        if (!this.currentConvId) return;
        try {
            const res = await authFetch(`/api/scene/${this.currentConvId}`);
            if (!res.ok) return;
            this.applyScene(await res.json());
        } catch (e) {}
    }
    applyScene(scene) {
        if (!scene) return;
        this.coLocated = !!scene.co_located;
        if (typeof scene.paused === 'boolean' && scene.paused !== this.paused) {
            this._setPaused(scene.paused);
        }
        const yuxiaLoc = scene.location || '';
        const playerLoc = scene.player_location || yuxiaLoc;
        if (this.locationTag) {
            this.locationTag.textContent = this.coLocated
                ? yuxiaLoc
                : `${yuxiaLoc}（你在${playerLoc}）`;
        }
        const desc = this.coLocated
            ? (scene.player_description || scene.description)
            : (scene.player_description || scene.description);
        if (this.sceneDesc && desc) {
            this.sceneDesc.textContent = desc;
            if (this.sceneCard) this.sceneCard.style.display = 'block';
        }
        if (scene.locations && this.locationPicker) {
            const items = scene.locations
                .map(name => `<li data-loc="${this._e(name)}"${name === playerLoc ? ' class="current"' : ''}>${this._e(name)}</li>`)
                .join('');
            // 在一起的时候可以"带她一起走"
            const together = this.coLocated
                ? `<li class="together-toggle"><label>` +
                  `<input type="checkbox" id="moveTogether"${this.moveTogether ? ' checked' : ''}>` +
                  `<span>带尤夏一起去</span></label></li>`
                : '';
            this.locationPicker.innerHTML = together + items +
                '<li class="new-loc"><input id="newLocationInput" placeholder="去没去过的地方…" ' +
                'maxlength="20" autocomplete="off"></li>';
        }
        if (scene.story_time && window.emotionAssistant && window.emotionAssistant.eventPanel) {
            window.emotionAssistant.eventPanel.updateStoryTime(scene.story_time);
        }
        this._updatePlaceholder();

        // 环境描写在后台生成：拿到 pending 就稍后重取（最多 6 次，避免无限轮询）
        if (scene.pending) {
            this._sceneRetries = (this._sceneRetries || 0) + 1;
            if (this._sceneRetries <= 6) {
                clearTimeout(this._sceneRetryTimer);
                this._sceneRetryTimer = setTimeout(() => this.loadScene(), 1800);
            }
        } else {
            this._sceneRetries = 0;
            clearTimeout(this._sceneRetryTimer);
        }
    }
    async _toggleLocationPicker(force) {
        if (!this.locationPicker) return;
        const wantShow = force === undefined
            ? this.locationPicker.style.display !== 'block'
            : force;
        if (!wantShow) {
            this.locationPicker.style.display = 'none';
            return;
        }
        // 还没有会话时先建一个，否则拿不到地点列表（之前会弹出空下拉）
        if (!this.currentConvId) await this.ensureConversation();
        if (this.locationPicker.children.length === 0) {
            // 首次打开要生成场景描写，先给个反馈，别让用户以为没反应
            this.locationPicker.innerHTML = '<li class="loading">场景加载中…</li>';
            this.locationPicker.style.display = 'block';
            await this.loadScene();
        }
        this.locationPicker.style.display = 'block';
    }

    async ensureConversation() {
        if (this.currentConvId) return this.currentConvId;
        try {
            const res = await authFetch('/api/history/new', { method: 'POST' });
            const data = await res.json();
            if (data && data.id) {
                this.currentConvId = data.id;
                if (window.emotionAssistant) window.emotionAssistant.onConvIdChange(data.id);
            }
        } catch (e) {}
        return this.currentConvId;
    }

    _setSceneLoading(on) {
        if (!this.sceneCard || !this.sceneDesc) return;
        if (on) {
            this.sceneCard.style.display = 'block';
            this.sceneDesc.textContent = '正在切换场景…';
        }
    }

    async moveTo(name) {
        if (!name) return;
        if (!this.currentConvId) await this.ensureConversation();
        if (!this.currentConvId) return;
        // 勾了"带尤夏一起去"就两人一起走（异地时这个勾选无效）
        const both = this.moveTogether && this.coLocated;
        this._setSceneLoading(true);
        try {
            const res = await authFetch(`/api/move/${this.currentConvId}`, {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ who: both ? 'both' : 'player', location: name }),
            });
            if (!res.ok) return;
            this.applyScene(await res.json());
        } catch (e) {
        } finally {
            this._setSceneLoading(false);
        }
    }

    async sendMessage(forceChannel) {
        const message = this.inputEl.value.trim();
        if (!message || this.isProcessing) return;
        this._lastRenderedCount = 0;
        this._renderedObjects = 0;
        this._lastChannel = forceChannel || (this.coLocated ? 'chat' : 'phone');
        this.isProcessing = true; this._disableInput();
        this.inputEl.value = ''; this.inputEl.style.height = 'auto';
        if (this.welcomeEl) { AnimationEngine.hideWelcome(); this.welcomeEl = null; }
        const displayMsg = this.pushMode ? (message || '（轻戳尤夏）') : message;
        const serverMsg = this.pushMode ? `/推进 ${message}` : message;
        const isPushCmd = this.pushMode;
        if (this.pushMode) this.togglePushMode();
        const viaPhone = forceChannel === 'phone' || !this.coLocated;
        if (viaPhone) {
            if (window.phonePanel) {
                window.phonePanel.push('user', displayMsg, false);
                window.phonePanel.showTyping();
            }
        } else {
            this._addBubble(this._looksLikeSceneNote(displayMsg) ? 'narration' : 'user', displayMsg);
        }
        AnimationEngine.showThinking(this.messagesContainer);
        try {
            const payload = { conversation_id: this.currentConvId, message: serverMsg };
            if (forceChannel) payload.channel = forceChannel;
            const response = await authFetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload) });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const reader = response.body.getReader(); const decoder = new TextDecoder();
            let fullText = '', atmData = null;
            AnimationEngine.hideThinking();

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                for (const line of decoder.decode(value, { stream: true }).split('\n')) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.type === 'conv_id') {
                            this.currentConvId = data.id;
                            if (window.emotionAssistant) window.emotionAssistant.onConvIdChange(data.id);
                        } else if (data.type === 'chunk') {
                            fullText += data.content;
                            this._renderStreamingJSON(fullText);
                        } else if (data.type === 'story_time') {
                            this.storyTime = data.content;
                            if (window.emotionAssistant && window.emotionAssistant.eventPanel)
                                window.emotionAssistant.eventPanel.updateStoryTime(data.content);
                        } else if (data.type === 'scene_changed' && data.scene) {
                            this.applyScene(data.scene);
                        } else if (data.type === 'done') {
                            if (data.channel) this._lastChannel = data.channel;
                            atmData = { conv_id: data.conv_id, msg_count: data.msg_count, is_push_cmd: data.is_push_cmd || isPushCmd };
                        }
                    } catch (e) {}
                }
            }

            this._finalizeStreamingJSON(fullText);

            if (atmData) {
                // 暂停时只刷新场景，不推进世界
                if (this.paused) this.loadScene();
                else this._fetchAtmosphere(atmData.conv_id, atmData.msg_count, atmData.is_push_cmd);
            }
        } catch (error) {
            AnimationEngine.hideThinking();
            if (window.phonePanel) window.phonePanel.hideTyping();
            this._addBubble('assistant', `Error: ${error.message}`);
            this.isProcessing = false; this._enableInput();
            return;
        }
        this.isProcessing = false; this._enableInput();
        this.loadScene();
        if (window.emotionAssistant && window.emotionAssistant.historyManager) window.emotionAssistant.historyManager.loadHistory();
    }

    // ── JSON 流式渲染 ──
    _lastRenderedCount = 0;
    _renderedObjects = 0;
    _lastChannel = 'chat';

    _renderStreamingJSON(fullText) {
        const lines = fullText.split('\n').filter(l => l.trim());
        // 只推进到"成功解析"的最后一行：遇到半个 JSON 就停下，等下一个 chunk
        while (this._lastRenderedCount < lines.length) {
            const trimmed = lines[this._lastRenderedCount].trim();
            if (!trimmed.startsWith('{')) {
                this._lastRenderedCount++;
                continue;
            }
            let obj = null;
            try {
                obj = JSON.parse(trimmed);
            } catch (e) {
                break;  // 还没收到完整对象
            }
            this._renderJSONObject(obj);
            this._renderedObjects++;
            this._lastRenderedCount++;
        }
    }

    _finalizeStreamingJSON(fullText) {
        // 先把流式阶段卡住的部分补上
        this._renderStreamingJSON(fullText);

        let rendered = this._renderedObjects;

        // 模型把 JSON 折成多行时，按括号配对整段提取后补渲染
        if (rendered === 0 && fullText.trim()) {
            const objs = this._extractJSONObjects(fullText);
            objs.forEach(obj => this._renderJSONObject(obj));
            rendered = objs.length;
            this._renderedObjects += objs.length;
        }

        // 最终兜底：模型没按 JSON 输出（例如思考模式跑偏、纯文本回复、API 报错），
        // 按当前通道原样展示，绝不能让消息"静默消失"
        if (rendered === 0 && fullText.trim()) {
            const text = fullText.trim();
            const toPhone = this._lastChannel === 'phone' || !this.coLocated;
            if (toPhone && window.phonePanel) {
                window.phonePanel.push('assistant', text, true);
            } else {
                this._addBubble('assistant', text);
            }
        }
        document.querySelectorAll('.stream-temp').forEach(el => el.classList.remove('stream-temp'));
        if (window.phonePanel) window.phonePanel.hideTyping();
    }

    _extractJSONObjects(text) {
        const out = [];
        let depth = 0, start = -1, inStr = false, esc = false;
        for (let i = 0; i < text.length; i++) {
            const ch = text[i];
            if (inStr) {
                if (esc) esc = false;
                else if (ch === '\\') esc = true;
                else if (ch === '"') inStr = false;
                continue;
            }
            if (ch === '"') { inStr = true; continue; }
            if (ch === '{') { if (depth === 0) start = i; depth++; }
            else if (ch === '}') {
                depth--;
                if (depth === 0 && start >= 0) {
                    try { out.push(JSON.parse(text.slice(start, i + 1))); } catch (e) { /* 忽略损坏片段 */ }
                    start = -1;
                }
            }
        }
        return out;
    }

    _renderJSONObject(obj) {
        // 主页面只呈现「对白」和「表情包」；动作与神态由系统的场景描写负责
        if (obj["对白"]) {
            this._addBubble('assistant', obj["对白"], true);
        }

        // 手机消息：不进主页面，交给手机面板并亮红点
        if (obj["消息"]) {
            if (window.phonePanel) window.phonePanel.push('assistant', obj["消息"], false);
        }

        // 表情包
        if (obj["表情包"]) {
            String(obj["表情包"]).split(/[,，]/).filter(Boolean).forEach(name => this._addSticker(name.trim()));
        }

        AnimationEngine.scrollToBottom(this.messagesContainer);
    }

    // ── 异步氛围获取 ──
    async _fetchAtmosphere(convId, msgCount, isPushCmd) {
        this._addEventOrganizing();
        try {
            const response = await authFetch(`/api/atmosphere/${convId}`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ msg_count: msgCount, is_push_cmd: isPushCmd })
            });
            if (!response.ok) { this._removeEventOrganizing(); return; }
            const reader = response.body.getReader(); const decoder = new TextDecoder();
            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                for (const line of decoder.decode(value, { stream: true }).split('\n')) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.type === 'location') {
                            this.locationTag.textContent = data.content;
                        } else if (data.type === 'yuxia_state') {
                            this._renderYuxiaState(data.content);
                        } else if (data.type === 'scene_discovered') {
                            this._addWorldNarration(
                                (data.created ? '发现新场景：' : '进入场景：') + data.location,
                                'low');
                        } else if (data.type === 'scene_changed' && data.scene) {
                            this.applyScene(data.scene);
                        } else if (data.type === 'autonomy') {
                            // 尤夏自己开口 / 发消息 / 换场景 / 动手做点什么
                            if (data.action === 'move') {
                                this._addWorldNarration(data.content || '尤夏走开了', 'low');
                                this.loadScene();
                            } else if (data.action === 'lead') {
                                // 她拉着你一起换地方
                                this._addWorldNarration(data.content || '尤夏拉着你走了', 'low');
                                this.loadScene();
                            } else if (data.action === 'act') {
                                if (data.content) this._addWorldNarration(this._asYuxiaAction(data.content), 'low', '尤夏');
                            } else if (data.channel === 'phone' || !this.coLocated) {
                                if (window.phonePanel) window.phonePanel.push('assistant', data.content, true);
                            } else {
                                this._addBubble('assistant', data.content);
                            }
                        } else if (data.type === 'world_narration') {
                            this._addWorldNarration(data.content, data.intensity || 'low');
                        } else if (data.type === 'event_organizing') {
                            // 更新提示文字
                            const hint = document.getElementById('eventOrganizingHint');
                            if (hint) {
                                const label = hint.querySelector('.world-narration-label');
                                if (label && data.hint) label.textContent = data.hint;
                            } else {
                                this._addEventOrganizing();
                            }
                        } else if (data.type === 'story_time') {
                            if (window.emotionAssistant && window.emotionAssistant.eventPanel)
                                window.emotionAssistant.eventPanel.updateStoryTime(data.content);
                            // 时间跳跃时给一条提示，让玩家知道时间被推进了
                            if (data.jump && data.jump !== 'NONE' && data.from && data.to) {
                                this._addWorldNarration(`时间流转：${data.from} → ${data.to}`, 'low');
                            }
                        } else if (data.type === 'story_summary') {
                            if (window.emotionAssistant && window.emotionAssistant.eventPanel)
                                window.emotionAssistant.eventPanel.updateSummary(data.content);
                        } else if (data.type === 'event_update') {
                            if (window.emotionAssistant && window.emotionAssistant.eventPanel)
                                window.emotionAssistant.eventPanel.renderEvents(data.events, data.has_push, data.push_hint);
                        } else if (data.type === 'done') {
                            this._removeEventOrganizing();
                        }
                    } catch (e) {}
                }
            }
        } catch (e) {
            this._removeEventOrganizing();
        }
    }

    // ── 渲染方法 ──
    _addBubble(role, content, isStream) {
        const row = document.createElement('div');
        const isNote = role === 'narration';
        row.className = isNote ? 'message-row narration-row' : `message-row ${role}`;
        if (isStream) row.classList.add('stream-temp');
        const bubble = document.createElement('div');
        bubble.className = isNote ? 'scene-note' : 'message-bubble';
        bubble.textContent = content;
        row.appendChild(bubble);
        this.messagesContainer.appendChild(row);
        AnimationEngine.slideIn(row);
        AnimationEngine.scrollToBottom(this.messagesContainer);
        return bubble;
    }

    _addMessageBubble(content) {
        // 兼容旧数据：以前存在主聊天里的手机消息，直接丢给手机面板
        if (window.phonePanel) window.phonePanel.push('assistant', content, false);
    }

    async _addSticker(emotion) {
        // 后端按「情绪词 → 表情包标签/语义」匹配，前端只负责渲染
        const wrapper = document.createElement('div');
        wrapper.className = 'message-row assistant sticker-row stream-temp';
        wrapper.innerHTML = `<div class="sticker-fallback">[${emotion}]</div>`;
        this.messagesContainer.appendChild(wrapper);
        AnimationEngine.scrollToBottom(this.messagesContainer);

        try {
            const res = await fetch(`/api/stickers/match?q=${encodeURIComponent(emotion)}&k=3`);
            const data = await res.json();
            const pick = (data.matches || [])[0];
            if (!pick || !pick.url) return;  // 没有素材就保留文字兜底

            const img = document.createElement('img');
            img.className = 'sticker-img';
            img.src = pick.url;
            img.alt = pick.emotion || emotion;
            img.title = [pick.emotion, pick.tags].filter(Boolean).join(' / ') || emotion;
            img.onerror = () => { /* 保持文字兜底 */ };
            img.onload = () => {
                wrapper.innerHTML = '';
                wrapper.appendChild(img);
                AnimationEngine.fadeIn(img);
                AnimationEngine.scrollToBottom(this.messagesContainer);
            };
        } catch (e) {
            // 静默失败，保留文字兜底
        }
    }

    _addEventOrganizing() {
        const existing = document.getElementById('eventOrganizingHint');
        if (existing) return;
        const wrapper = document.createElement('div');
        wrapper.className = 'world-narration';
        wrapper.id = 'eventOrganizingHint';
        wrapper.innerHTML = '<div class="world-narration-inner"><div class="world-narration-label">事件组织中...</div></div>';
        this.messagesContainer.appendChild(wrapper);
        AnimationEngine.scrollToBottom(this.messagesContainer);
    }

    _removeEventOrganizing() {
        const hint = document.getElementById('eventOrganizingHint');
        if (hint) {
            hint.style.opacity = '0';
            hint.style.transform = 'scale(0.95)';
            hint.style.transition = 'opacity 0.3s, transform 0.3s';
            setTimeout(() => hint.remove(), 300);
        }
    }

    // 自主行动 act：模型写的是"她在做什么"，补上主语后再当旁白渲染
    _asYuxiaAction(text) {
        let t = (text || '').trim();
        if (!t) return '尤夏动了动。';
        if (/^我/.test(t)) return '她' + t.slice(1);          // 第一人称兜底
        return /^(她|尤夏)/.test(t) ? t : `尤夏${t}`;
    }

    _addWorldNarration(text, intensity, label = '世界') {
        this._removeEventOrganizing();
        const wrapper = document.createElement('div');
        wrapper.className = `world-narration ${intensity === 'medium' ? 'medium' : ''}`.trim();
        wrapper.dataset.label = label;
        // 连续的同类旁白只保留第一个标签，避免一屏三个「— 世界 —」
        const prev = this.messagesContainer.lastElementChild;
        if (prev && prev.classList.contains('world-narration') && prev.dataset.label === label) {
            wrapper.classList.add('cont');
        }
        wrapper.innerHTML = `<div class="world-narration-inner"><div class="world-narration-label">— ${label} —</div><div class="world-narration-text">${text}</div></div>`;
        this.messagesContainer.appendChild(wrapper);
        AnimationEngine.scrollToBottom(this.messagesContainer);
    }

    async loadConversation(convId) {
        this.currentConvId = convId;
        this._lastRenderedCount = 0;
        this.messagesContainer.innerHTML = '';
        try {
            const res = await authFetch(`/api/history/${convId}`);
            const data = await res.json();
            const msgs = (data.messages || []).filter(m => (m.channel || 'chat') !== 'phone');
            const chatOnly = msgs.filter(m => (m.channel || 'chat') !== 'state');
            if (chatOnly.length > 0) {
                msgs.forEach((msg) => {
                    const ch = msg.channel || 'chat';
                    if (ch === 'state') {
                        this._renderYuxiaState(msg.content);   // 只保留最后一条神态描写
                    } else if (msg.role === 'user') {
                        this._addBubble(this._looksLikeSceneNote(msg.content) ? 'narration' : 'user', msg.content);
                    } else {
                        this._parseAndRenderHistory(msg.content);
                    }
                });
            } else {
                this._showWelcome();
            }
            if (window.phonePanel) window.phonePanel.setMessages(data.phone || [], data.unread || 0);
            if (data.location) {
                this.applyScene({
                    location: data.location,
                    player_location: data.player_location,
                    co_located: data.co_located,
                    story_time: data.story_time,
                });
            }
            this.loadScene();
            if (window.emotionAssistant && window.emotionAssistant.eventPanel) {
                if (data.story_time) window.emotionAssistant.eventPanel.updateStoryTime(data.story_time);
                if (data.story_summary) window.emotionAssistant.eventPanel.updateSummary(data.story_summary);
                if (data.events && data.events.length > 0) {
                    window.emotionAssistant.eventPanel.renderEvents(data.events, false, '');
                } else if (data.messages && data.messages.length > 0 && !data.events_initialized) {
                    window.emotionAssistant.eventPanel.showInitializing();
                    this._initEventsForConversation(convId);
                }
            }
        } catch (e) { this._showWelcome(); }
        AnimationEngine.scrollToBottom(this.messagesContainer);
    }

    _looksLikeSceneNote(text) {
        return /^（.*）$/.test((text || '').trim());
    }

    _parseAndRenderHistory(content) {
        const lines = content.split('\n').filter(l => l.trim());
        let parsedAny = false;
        for (const line of lines) {
            const trimmed = line.trim();
            if (!trimmed.startsWith('{')) continue;
            try {
                const obj = JSON.parse(trimmed);
                if (obj["对白"]) this._addBubble('assistant', obj["对白"]);
                if (obj["消息"] && window.phonePanel) window.phonePanel.push('assistant', obj["消息"], false);
                if (obj["表情包"]) {
                    String(obj["表情包"]).split(/[,，]/).filter(Boolean)
                        .forEach(name => this._addSticker(name.trim()));
                }
                parsedAny = true;
            } catch (e) {}
        }
        if (!parsedAny) this._addBubble('assistant', content);
    }

    async _initEventsForConversation(convId) {
        try {
            const res = await authFetch(`/api/events/init/${convId}`, { method: 'POST' });
            const data = await res.json();
            if (data.success && window.emotionAssistant && window.emotionAssistant.eventPanel) {
                if (data.story_time) window.emotionAssistant.eventPanel.updateStoryTime(data.story_time);
                if (data.story_summary) window.emotionAssistant.eventPanel.updateSummary(data.story_summary);
                if (data.events) window.emotionAssistant.eventPanel.renderEvents(data.events, data.has_push, data.push_hint);
            } else { window.emotionAssistant.eventPanel.showEmpty(); }
        } catch (e) { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.showEmpty(); }
    }

    newConversation() {
        this.currentConvId = ''; this.messagesContainer.innerHTML = '';
        this.coLocated = true;
        if (this.sceneCard) this.sceneCard.style.display = 'none';
        if (window.phonePanel) window.phonePanel.setMessages([], 0);
        this._showWelcome();
        if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.clear();
        this.inputEl.focus();
    }

    _e(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }

    _renderYuxiaState(text) {
        if (!text) return;
        document.querySelectorAll('.yuxia-state-row').forEach(el => el.remove());
        const row = document.createElement('div');
        row.className = 'message-row yuxia-state-row';
        const box = document.createElement('div');
        box.className = 'yuxia-state';
        box.textContent = text;
        row.appendChild(box);
        this.messagesContainer.appendChild(row);
        AnimationEngine.fadeIn(box);
        AnimationEngine.scrollToBottom(this.messagesContainer);
    }

    _showWelcome() {
        this.welcomeEl = document.createElement('div');
        this.welcomeEl.className = 'welcome-message';
        this.welcomeEl.innerHTML = `<svg class="welcome-icon" viewBox="0 0 64 64" width="56" height="56" fill="none" stroke="#6366f1" stroke-width="2.5" stroke-linecap="round"><path d="M32 8C19 8 8 17 8 28c0 10 8 17 16 19v9l10-8c12 0 22-9 22-20S45 8 32 8z"/><circle cx="22" cy="28" r="2.5" fill="#6366f1"/><circle cx="42" cy="28" r="2.5" fill="#6366f1"/></svg>`;
        this.messagesContainer.appendChild(this.welcomeEl);
    }
}

// ── EventPanel / HistoryManager / UIController ──

/** 手机面板：所有手机通道消息都在这里，主聊天页不出现
 *  面板是可拖动、可缩放的浮动窗口（位置/尺寸记在本机 localStorage） */
class PhonePanel {
    constructor() {
        this.btn = document.getElementById('btnPhone');
        this.badge = document.getElementById('phoneBadge');
        this.overlay = document.getElementById('phoneOverlay');
        this.shell = document.getElementById('phoneShell');
        this.header = document.getElementById('phoneHeader');
        this.resizeHandle = document.getElementById('phoneResize');
        this.list = document.getElementById('phoneList');
        this.input = document.getElementById('phoneInput');
        this.btnSend = document.getElementById('phoneSend');
        this.btnClose = document.getElementById('phoneClose');
        this.messages = [];
        this.unread = 0;
        this.isOpen = false;
        this._win = null;
        this._bind();
        this._initWindow();
    }
    _bind() {
        if (this.btn) this.btn.addEventListener('click', () => this.toggle());
        if (this.btnClose) this.btnClose.addEventListener('click', () => this.close());
        if (this.btnSend) this.btnSend.addEventListener('click', () => this.send());
        if (this.input) {
            this.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.send(); }
            });
        }
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.isOpen) this.close();
        });
    }

    // ── 浮动窗口：拖动 / 缩放 / 位置记忆 ──
    _initWindow() {
        if (!this.shell) return;
        const saved = this._loadWindow();
        this._applyWindow(saved || this._defaultWindow());
        this._bindDrag();
        this._bindResize();
        window.addEventListener('resize', () => {
            if (!this.isOpen) return;
            this._applyWindow(this._clamp(this._currentWindow()));
        });
    }
    _defaultWindow() {
        const w = 340, h = Math.min(480, window.innerHeight - 120);
        return {
            left: Math.max(12, window.innerWidth - w - 48),
            top: 72,
            width: w,
            height: Math.max(260, h),
        };
    }
    _loadWindow() {
        try {
            const raw = localStorage.getItem('yuxia_phone_win');
            if (!raw) return null;
            const d = JSON.parse(raw);
            if (!d || typeof d.left !== 'number') return null;
            return d;
        } catch (e) { return null; }
    }
    _saveWindow(win) {
        try { localStorage.setItem('yuxia_phone_win', JSON.stringify(win)); } catch (e) {}
    }
    _currentWindow() {
        return {
            left: parseFloat(this.shell.style.left) || 16,
            top: parseFloat(this.shell.style.top) || 72,
            width: parseFloat(this.shell.style.width) || 340,
            height: parseFloat(this.shell.style.height) || 480,
        };
    }
    // 不让窗口跑出视口（留 8px 边距），宽高也不超过视口
    _clamp(win) {
        const margin = 8;
        const vw = window.innerWidth, vh = window.innerHeight;
        const width = Math.min(Math.max(260, win.width), Math.max(260, vw - margin * 2));
        const height = Math.min(Math.max(220, win.height), Math.max(220, vh - margin * 2));
        const left = Math.min(Math.max(margin, win.left), Math.max(margin, vw - width - margin));
        const top = Math.min(Math.max(margin, win.top), Math.max(margin, vh - height - margin));
        return { left, top, width, height };
    }
    _applyWindow(win) {
        const w = this._clamp(win);
        this._win = w;
        this.shell.style.left = w.left + 'px';
        this.shell.style.top = w.top + 'px';
        this.shell.style.width = w.width + 'px';
        this.shell.style.height = w.height + 'px';
    }
    _bindDrag() {
        if (!this.header) return;
        let start = null;
        const onMove = (e) => {
            if (!start) return;
            const win = {
                ...start.win,
                left: start.win.left + (e.clientX - start.x),
                top: start.win.top + (e.clientY - start.y),
            };
            this._applyWindow(win);
            e.preventDefault();
        };
        const onUp = () => {
            if (!start) return;
            start = null;
            this.shell.classList.remove('dragging');
            this._saveWindow(this._currentWindow());
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
        };
        this.header.addEventListener('pointerdown', (e) => {
            if (e.target.closest('button')) return;   // 别把关闭按钮当拖拽区
            if (window.innerWidth <= 768) return;     // 手机上不浮动
            start = { x: e.clientX, y: e.clientY, win: this._currentWindow() };
            this.shell.classList.add('dragging');
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onUp);
            e.preventDefault();
        });
    }
    _bindResize() {
        if (!this.resizeHandle) return;
        let start = null;
        const onMove = (e) => {
            if (!start) return;
            this._applyWindow({
                left: start.win.left,
                top: start.win.top,
                width: start.win.width + (e.clientX - start.x),
                height: start.win.height + (e.clientY - start.y),
            });
            e.preventDefault();
        };
        const onUp = () => {
            if (!start) return;
            start = null;
            this.shell.classList.remove('dragging');
            this._saveWindow(this._currentWindow());
            window.removeEventListener('pointermove', onMove);
            window.removeEventListener('pointerup', onUp);
        };
        this.resizeHandle.addEventListener('pointerdown', (e) => {
            if (window.innerWidth <= 768) return;
            start = { x: e.clientX, y: e.clientY, win: this._currentWindow() };
            this.shell.classList.add('dragging');
            window.addEventListener('pointermove', onMove);
            window.addEventListener('pointerup', onUp);
            e.preventDefault();
        });
    }
    setMessages(msgs, unread) {
        this.messages = msgs || [];
        this.render();
        this.setUnread(unread || 0);
    }
    push(role, text, markUnread) {
        this.messages.push({ role, content: text });
        if (role === 'assistant') this.hideTyping();
        this.render();
        if (markUnread && !this.isOpen) this.setUnread(this.unread + 1);
    }
    showTyping() {
        if (!this.list || document.getElementById('phoneTyping')) return;
        const el = document.createElement('div');
        el.id = 'phoneTyping';
        el.className = 'phone-msg her phone-typing';
        el.innerHTML = '<span></span><span></span><span></span>';
        this.list.appendChild(el);
        this.list.scrollTop = this.list.scrollHeight;
    }
    hideTyping() {
        const el = document.getElementById('phoneTyping');
        if (el) el.remove();
    }
    setUnread(n) {
        this.unread = n;
        if (!this.badge) return;
        this.badge.textContent = n > 99 ? '99+' : String(n);
        this.badge.style.display = n > 0 ? 'flex' : 'none';
    }
    render() {
        if (!this.list) return;
        this.list.innerHTML = this.messages.length === 0
            ? '<div class="phone-empty">还没有消息</div>'
            : this.messages.map(m => `<div class="phone-msg ${m.role === 'user' ? 'me' : 'her'}">${this._e(m.content)}</div>`).join('');
        this.list.scrollTop = this.list.scrollHeight;
    }
    async send() {
        const text = (this.input.value || '').trim();
        if (!text) return;
        this.input.value = '';
        const cm = window.emotionAssistant && window.emotionAssistant.chatManager;
        if (!cm) return;
        cm.inputEl.value = text;
        await cm.sendMessage('phone');
    }
    openPhone() {
        if (!this.overlay) return;
        this._applyWindow(this._clamp(this._win || this._currentWindow()));
        this.overlay.classList.add('show');
        this.isOpen = true;
        this.setUnread(0);
        this._markRead();
        if (this.input) this.input.focus();
    }
    close() {
        if (!this.overlay) return;
        this.overlay.classList.remove('show');
        this.isOpen = false;
    }
    toggle() { this.isOpen ? this.close() : this.openPhone(); }
    async _markRead() {
        const cm = window.emotionAssistant && window.emotionAssistant.chatManager;
        if (!cm || !cm.currentConvId) return;
        try { await authFetch(`/api/phone/read/${cm.currentConvId}`, { method: 'POST' }); } catch (e) {}
    }
    _e(s) { const d = document.createElement('div'); d.textContent = s == null ? '' : s; return d.innerHTML; }
}

class EventPanel {
    constructor() { this.panel = document.getElementById('eventPanel'); this.storyTimeEl = document.querySelector('#eventStoryTime .time-text'); this.summaryEl = document.getElementById('eventSummary'); this.eventList = document.getElementById('eventList'); this.pushHint = document.getElementById('eventPushHint'); this.pushHintText = document.getElementById('pushHintText'); this.btnClose = document.getElementById('btnEventClose'); if (this.btnClose) this.btnClose.addEventListener('click', () => this.close()); }
    init(cfg) { this.cfg = cfg; this.updateStoryTime(cfg.default_story_time); this.updateSummary(cfg.default_story_summary); }
    updateStoryTime(t) { if (this.storyTimeEl && t) this.storyTimeEl.textContent = t; }
    updateSummary(s) { if (this.summaryEl && s) this.summaryEl.textContent = s; }
    showInitializing() { this.updateStoryTime('分析中...'); this.summaryEl.textContent = '正在分析历史对话...'; this.eventList.innerHTML = '<div class="event-empty event-loading">AI 正在分析历史对话...</div>'; }
    showEmpty() { this.eventList.innerHTML = '<div class="event-empty">暂无事件记录，开始对话吧</div>'; }
    _ensureVisible() { this.panel.classList.remove('collapsed'); this.panel.offsetHeight; if (window.innerWidth <= 768) this.panel.classList.add('open'); }
    renderEvents(events, hasPush, pushHint) {
        if (!events || events.length === 0) return;
        this._ensureVisible();
        const groups = this._groupByDay(events);
        const dayKeys = Object.keys(groups);
        if (dayKeys.length === 0) return;
        const latestDay = dayKeys[dayKeys.length - 1];
        let html = '';
        dayKeys.forEach((day, gi) => {
            const dayEvents = groups[day];
            const count = dayEvents.length;
            const isExpanded = day === latestDay || dayKeys.length <= 2;
            html += `<div class="day-group${isExpanded ? ' expanded' : ''}" data-day="${this._e(day)}">`;
            html += `<div class="day-group-header" onclick="this.parentElement.classList.toggle('expanded')">`;
            html += `<span class="day-arrow"></span>`;
            html += `<span class="day-label">${this._e(day)}</span>`;
            html += `<span class="day-count">${count} 个事件</span>`;
            html += `</div><div class="day-group-body">`;
            dayEvents.forEach((ev, i) => {
                if (ev.event_type === 'time_advance') {
                    const t = ev.story_time ? this._extractTime(ev.story_time) : '';
                    html += `<div class="event-time-divider"><span class="time-label">${this._e(t || ev.title)}</span></div>`;
                    return;
                }
                const iconSvg = SVG_ICONS[ev.event_type] || SVG_ICONS.misc;
                const typeClass = ` event-${ev.event_type}`;
                const statusClass = `status-${ev.status}`;
                const title = ev.event_type === 'milestone' ? `✦ ${this._e(ev.title)}` : this._e(ev.title);
                const timeLabel = ev.story_time ? this._extractTime(ev.story_time) : '';
                html += `<div class="event-card${typeClass} ${statusClass}" style="animation-delay: ${i * 0.03}s">`;
                html += `<div class="event-card-header"><span class="event-type-icon">${iconSvg}</span>`;
                html += `<span class="event-card-title">${title}</span>`;
                html += `<span class="event-status-dot ${ev.status}"></span></div>`;
                html += `<div class="event-card-desc">${this._e(ev.description)}</div>`;
                if (timeLabel) html += `<div class="event-card-time">${timeLabel}</div>`;
                html += `</div>`;
            });
            html += `</div></div>`;
        });
        if (events.length > 30) {
            html += `<button class="event-expand-btn" onclick="document.querySelectorAll('.day-group').forEach(g=>g.classList.add('expanded'));this.style.display='none'">📋 展开全部历史事件</button>`;
        }
        this.eventList.innerHTML = html;
        if (hasPush && pushHint) { this.pushHint.style.display = 'block'; this.pushHintText.textContent = pushHint; }
        else if (pushHint) { this.pushHint.style.display = 'block'; this.pushHintText.textContent = pushHint; }
        else { this.pushHint.style.display = 'none'; }
        this.eventList.scrollTop = this.eventList.scrollHeight;
    }
    _groupByDay(events) {
        const groups = {};
        const dayRe = /(第[^\s]+天)/;
        events.forEach(ev => {
            const m = ev.story_time ? ev.story_time.match(dayRe) : null;
            const day = m ? m[1] : '未分组';
            if (!groups[day]) groups[day] = [];
            groups[day].push(ev);
        });
        return groups;
    }
    _extractTime(storyTime) {
        const m = storyTime.match(/[早上下晚凌][晨午晚]?\s*\d{1,2}:\d{2}/);
        if (m) return m[0];
        const m2 = storyTime.match(/\d{1,2}:\d{2}/);
        return m2 ? m2[0] : storyTime;
    }
    clear() { if (this.cfg) { this.updateStoryTime(this.cfg.default_story_time); this.updateSummary(this.cfg.default_story_summary); } else { this.updateStoryTime('第一天早晨'); this.updateSummary(''); } this.showEmpty(); this.pushHint.style.display = 'none'; }
    toggle() { if (window.innerWidth <= 768) this.panel.classList.toggle('open'); else this.panel.classList.toggle('collapsed'); }
    open() { this.panel.classList.remove('collapsed'); this.panel.classList.add('open'); }
    close() { if (window.innerWidth <= 768) this.panel.classList.remove('open'); else this.panel.classList.add('collapsed'); }
    _e(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
}

class HistoryManager {
    constructor(chatManager) { this.chatManager = chatManager; this.listEl = document.getElementById('historyList'); this.btnNewChat = document.getElementById('btnNewChat'); this._bindEvents(); }
    _bindEvents() { this.btnNewChat.addEventListener('click', () => { this.chatManager.newConversation(); this.loadHistory(); }); this.listEl.addEventListener('click', (e) => { const item = e.target.closest('.history-item'); if (!item) return; const convId = item.dataset.id; if (e.target.closest('.btn-delete')) { e.stopPropagation(); this.deleteConversation(convId); return; } this._setActive(convId); this.chatManager.loadConversation(convId); }); }
    async loadHistory() { try { const res = await authFetch('/api/history'); const conversations = await res.json(); this._render(conversations); } catch (e) { this.listEl.innerHTML = ''; } }
    _render(conversations) { if (!conversations || conversations.length === 0) { this.listEl.innerHTML = ''; return; } this.listEl.innerHTML = conversations.map((c, i) => `<li class="history-item ${c.id === this.chatManager.currentConvId ? 'active' : ''}" data-id="${c.id}" style="animation-delay: ${i * 0.04}s"><span class="conv-title">${this._e(c.title)}</span><span class="conv-date">${this._fd(c.updated_at)}</span><button class="btn-delete" title="删除"><svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2"><polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/></svg></button></li>`).join(''); }
    async deleteConversation(convId) { if (!confirm('Delete this conversation?')) return; try { await authFetch(`/api/history/${convId}`, { method: 'DELETE' }); if (convId === this.chatManager.currentConvId) this.chatManager.newConversation(); this.loadHistory(); } catch (e) { alert('Delete failed'); } }
    _setActive(convId) { this.listEl.querySelectorAll('.history-item').forEach((item) => item.classList.toggle('active', item.dataset.id === convId)); }
    _fd(dateStr) { if (!dateStr) return ''; const d = new Date(dateStr), now = new Date(), diff = now - d; if (diff < 60000) return 'now'; if (diff < 3600000) return Math.floor(diff / 60000) + 'm'; if (diff < 86400000) return Math.floor(diff / 3600000) + 'h'; return `${d.getMonth() + 1}/${d.getDate()}`; }
    _e(s) { const d = document.createElement('div'); d.textContent = s; return d.innerHTML; }
}

class UIController {
    constructor() {
        this.historyPanel = document.getElementById('historyPanel');
        this.overlay = document.getElementById('overlay');
        this.btnToggleSidebar = document.getElementById('btnToggleSidebar');
        this.btnRestoreSidebar = document.getElementById('btnRestoreSidebar');
        this.currentPanel = null;
        this._bindEvents();
        this._restoreSidebarState();
    }

    // 桌面端左侧栏收起 / 展开（状态记忆在 localStorage）
    _restoreSidebarState() {
        let saved = '0';
        try { saved = localStorage.getItem('yuxia_sidebar_collapsed') || '0'; } catch (e) {}
        this.setSidebarCollapsed(saved === '1', false);
    }
    setSidebarCollapsed(collapsed, persist = true) {
        if (this.historyPanel) this.historyPanel.classList.toggle('collapsed', collapsed);
        if (this.btnRestoreSidebar) this.btnRestoreSidebar.classList.toggle('show', collapsed);
        if (persist) {
            try { localStorage.setItem('yuxia_sidebar_collapsed', collapsed ? '1' : '0'); } catch (e) {}
        }
    }
    toggleSidebar() {
        const collapsed = this.historyPanel && this.historyPanel.classList.contains('collapsed');
        this.setSidebarCollapsed(!collapsed);
    }

    _bindEvents() { document.querySelectorAll('.nav-btn').forEach((btn) => btn.addEventListener('click', () => this.togglePanel(btn.dataset.panel))); this.overlay.addEventListener('click', () => this.closeAllPanels()); window.addEventListener('resize', () => { if (window.innerWidth > 768) this.closeAllPanels(); }); if (this.btnToggleSidebar) this.btnToggleSidebar.addEventListener('click', () => this.toggleSidebar()); if (this.btnRestoreSidebar) this.btnRestoreSidebar.addEventListener('click', () => this.setSidebarCollapsed(false)); const charTitle = document.getElementById('charTitle'); if (charTitle) { charTitle.style.pointerEvents = 'auto'; charTitle.style.cursor = 'pointer'; charTitle.title = '点击切换故事面板'; charTitle.addEventListener('click', (e) => { if (e.target.closest('#locationTag') || e.target.closest('#locationPicker')) return; if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.toggle(); }); } const btnLogout = document.getElementById('btnLogout'); if (btnLogout) btnLogout.addEventListener('click', async () => { await authFetch('/api/auth/logout', { method: 'POST' }); location.reload(); }); }
    togglePanel(panelName) { if (panelName === 'events') { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.toggle(); return; } if (panelName !== 'history') return; if (this.currentPanel === 'history') { this.closeAllPanels(); return; } this.closeAllPanels(); this.historyPanel.classList.add('open'); this.overlay.classList.add('show'); this.currentPanel = 'history'; }
    closeAllPanels() { this.historyPanel.classList.remove('open'); this.overlay.classList.remove('show'); this.currentPanel = null; }
}

/** API Key 设置面板：用户可覆盖服务器 config.py 中的 Key（仅本机 localStorage） */
class SettingsPanel {
    constructor() {
        this.overlay = document.getElementById('settingsOverlay');
        this.input = document.getElementById('apiKeyInput');
        this.hint = document.getElementById('apiKeyHint');
        this.hasServerKey = true;
        this._bindEvents();
    }
    _bindEvents() {
        const btnOpen = document.getElementById('btnSettings');
        if (btnOpen) btnOpen.addEventListener('click', () => this.open());
        const btnSave = document.getElementById('btnApiKeySave');
        if (btnSave) btnSave.addEventListener('click', () => this.save());
        const btnClear = document.getElementById('btnApiKeyClear');
        if (btnClear) btnClear.addEventListener('click', () => this.clear());
        const btnClose = document.getElementById('btnSettingsClose');
        if (btnClose) btnClose.addEventListener('click', () => this.close());
        if (this.overlay) this.overlay.addEventListener('click', (e) => { if (e.target === this.overlay) this.close(); });
        if (this.input) {
            this.input.addEventListener('keydown', (e) => {
                if (e.key === 'Enter') this.save();
                else if (e.key === 'Escape') this.close();
            });
        }
    }
    setServerKeyState(hasKey) {
        this.hasServerKey = !!hasKey;
        if (this.overlay && !this.overlay.classList.contains('hidden')) this._renderHint();
    }
    open() {
        this.input.value = (window.apiKeyManager && window.apiKeyManager.get()) || '';
        this._renderHint();
        this.overlay.classList.remove('hidden');
        this.input.focus();
    }
    close() { this.overlay.classList.add('hidden'); }
    save() {
        const value = this.input.value.trim();
        if (window.apiKeyManager) window.apiKeyManager.set(value);
        this._renderHint(value ? '已保存到本机浏览器，仅本设备生效' : '');
        if (value) setTimeout(() => this.close(), 700);
    }
    clear() {
        this.input.value = '';
        if (window.apiKeyManager) window.apiKeyManager.clear();
        this._renderHint('已清除，将使用服务器配置的 Key');
    }
    _renderHint(msg) {
        const isSet = !!(window.apiKeyManager && window.apiKeyManager.isSet());
        let text = msg;
        if (!text) {
            if (isSet) text = '当前使用你填写的 Key';
            else if (this.hasServerKey) text = '留空则使用服务器 config.py 中配置的 Key';
            else text = '服务器未配置 Key，请填写你自己的 DeepSeek Key';
        }
        this.hint.textContent = text;
        this.hint.className = 'settings-hint' + (isSet ? ' ok' : (this.hasServerKey ? '' : ' warn'));
    }
}

class EmotionAssistant {
    constructor() {
        this.chatManager = new ChatManager();
        this.historyManager = new HistoryManager(this.chatManager);
        this.eventPanel = new EventPanel();
        this.phonePanel = new PhonePanel();
        window.phonePanel = this.phonePanel;
        this.settingsPanel = new SettingsPanel();
        this.uiController = new UIController();
        window.emotionAssistant = this;
        this._idleTimer = null;
        this._init();
    }
    async _init() {
        await this._loadClientConfig();
        this.historyManager.loadHistory();
        document.getElementById('messageInput').focus();
        this._startIdlePolling();
    }
    _startIdlePolling() {
        if (this._idleTimer) { clearInterval(this._idleTimer); this._idleTimer = null; }
        // 暂停时完全不轮询：既省额度，也不会突然冒出她的行动
        const cm = this.chatManager;
        if (cm && cm.paused) return;
        // 每 N 秒问一次：她是否要自己接着做点什么（是否行动由服务端/模型判断）
        const seconds = this.idlePollSeconds || 20;
        this._idleTimer = setInterval(() => this._pollIdle(), seconds * 1000);
    }
    async _pollIdle() {
        const cm = this.chatManager;
        if (!cm || !cm.currentConvId || cm.isProcessing) return;
        if (cm.paused) return;
        if (document.hidden) return;
        try {
            const res = await authFetch(`/api/idle/${cm.currentConvId}`, { method: 'POST' });
            if (!res.ok) return;
            const data = await res.json();
            if (!data.acted) {
                if (data.unread) this.phonePanel.setUnread(data.unread);
                return;
            }
            if (data.action === 'move') {
                cm._addWorldNarration('尤夏走开了', 'low');
                if (data.scene) cm.applyScene(data.scene);
            } else if (data.action === 'lead') {
                cm._addWorldNarration(`尤夏拉着你去了${data.location || '别处'}`, 'low');
                if (data.scene) cm.applyScene(data.scene);
            } else if (data.action === 'act') {
                if (data.visible && data.text) cm._addWorldNarration(cm._asYuxiaAction(data.text), 'low', '尤夏');
                if (data.events && this.eventPanel) this.eventPanel.renderEvents(data.events, false, '');
            } else if (data.channel === 'phone') {
                this.phonePanel.push('assistant', data.text, true);
            } else {
                cm._addBubble('assistant', data.text);
            }
            if (data.story_time && this.eventPanel) this.eventPanel.updateStoryTime(data.story_time);
        } catch (e) {}
    }
    async _loadClientConfig() {
        try {
            const res = await fetch('/api/config');
            const cfg = await res.json();
            this.eventPanel.init(cfg);
            this.settingsPanel.setServerKeyState(cfg.has_server_key);
            if (cfg.idle_poll_seconds) {
                this.idlePollSeconds = cfg.idle_poll_seconds;
                this._startIdlePolling();   // 用服务端下发的间隔重启轮询
            }
        } catch (e) {
            this.eventPanel.init({ default_story_time: '第一天 07:00', default_story_summary: '与尤夏的故事，从这里开始...' });
        }
    }
    onConvIdChange(convId) { this.historyManager.loadHistory(); }
}

let auth, appInstance = null;
function onAuthSuccess() { if (!appInstance) appInstance = new EmotionAssistant(); }
document.addEventListener('DOMContentLoaded', async () => { auth = new AuthManager(); if (await auth.init()) onAuthSuccess(); });