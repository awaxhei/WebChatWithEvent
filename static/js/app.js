/**
 * AI 情感助手 - 前端类架构 (三 AI)
 */

// ============================================================
// 认证管理器类
// ============================================================
class AuthManager {
    constructor() {
        this.token = null;
        this.overlay = document.getElementById('loginOverlay');
        this.passwordInput = document.getElementById('loginPassword');
        this.rememberCheck = document.getElementById('rememberPwd');
        this.btnLogin = document.getElementById('btnLogin');
        this.errorEl = document.getElementById('loginError');
        this._bindEvents();
    }

    _bindEvents() {
        this.btnLogin.addEventListener('click', () => this._handleLogin());
        this.passwordInput.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this._handleLogin();
        });
    }

    async init() {
        const savedToken = localStorage.getItem('auth_token');
        if (savedToken) {
            this.token = savedToken;
            const valid = await this._verifyToken();
            if (valid) { this._hideOverlay(); return true; }
            localStorage.removeItem('auth_token');
            this.token = null;
        }
        this._showOverlay();
        return false;
    }

    async _verifyToken() {
        try {
            const res = await fetch('/api/auth/verify', { headers: { 'Authorization': `Bearer ${this.token}` } });
            const data = await res.json();
            return data.valid === true;
        } catch { return false; }
    }

    async _handleLogin() {
        const password = this.passwordInput.value;
        const remember = this.rememberCheck.checked;
        if (!password) { this._showError('请输入密码'); return; }
        this.btnLogin.disabled = true;
        this.errorEl.textContent = '';
        try {
            const res = await fetch('/api/auth/login', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password, remember })
            });
            const data = await res.json();
            if (data.success) {
                this.token = data.token;
                if (remember) localStorage.setItem('auth_token', data.token);
                else localStorage.removeItem('auth_token');
                this._hideOverlay();
                onAuthSuccess();
            } else {
                this._showError(data.error || '密码错误');
                this.passwordInput.value = '';
                this.passwordInput.focus();
            }
        } catch { this._showError('网络错误'); }
        this.btnLogin.disabled = false;
    }

    _showError(msg) {
        this.errorEl.textContent = msg;
        this.errorEl.style.animation = 'none';
        this.errorEl.offsetHeight;
        this.errorEl.style.animation = 'fadeIn 0.3s ease-out';
        setTimeout(() => { this.errorEl.textContent = ''; }, 3000);
    }

    _showOverlay() { this.overlay.classList.remove('hidden'); this.passwordInput.focus(); }
    _hideOverlay() { this.overlay.classList.add('hidden'); }
    getAuthHeaders() { return this.token ? { 'Authorization': `Bearer ${this.token}` } : {}; }
}

const authFetch = (url, options = {}) => {
    const headers = { ...options.headers, ...auth.getAuthHeaders() };
    return fetch(url, { ...options, headers });
};


// ============================================================
// SVG 图标定义
// ============================================================
const SVG_ICONS = {
    milestone: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6366f1" stroke-width="1.5"><polygon points="8 1.5 10 6 15 6 11.2 9 12.5 13.5 8 11 3.5 13.5 4.8 9 1 6 6 6"/></svg>',
    scene_change: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><path d="M3 8h10M8 3l5 5-5 5"/></svg>',
    emotional_shift: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><circle cx="5" cy="7" r="1"/><circle cx="11" cy="7" r="1"/><path d="M3 11c1.5 2 5.5 2 10 0"/></svg>',
    time_advance: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><circle cx="8" cy="8" r="6.5"/><polyline points="8 5 8 8 10.5 10.5"/></svg>',
    random: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#f59e0b" stroke-width="1.5"><rect x="2.5" y="2.5" width="11" height="11" rx="3"/><circle cx="5.5" cy="5.5" r="1.2"/><circle cx="10.5" cy="5.5" r="1.2"/><circle cx="8" cy="9" r="1"/></svg>',
    misc: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#a0aec0" stroke-width="1.5"><circle cx="8" cy="8" r="2"/><path d="M8 1v2m0 10v2M1 8h2m10 0h2"/></svg>',
};


// ============================================================
// 动画引擎类
// ============================================================
class AnimationEngine {
    static slideInMessage(element, delay = 0) {
        element.style.animation = 'none'; element.offsetHeight;
        element.style.animation = `messageSlideIn 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) ${delay}ms both`;
    }
    static scrollToBottom(container) {
        container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    }
    static showThinking(container) {
        const row = document.createElement('div');
        row.className = 'message-row assistant'; row.id = 'thinkingRow';
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble thinking-indicator';
        for (let i = 0; i < 3; i++) {
            const dot = document.createElement('span');
            dot.className = 'thinking-dot'; bubble.appendChild(dot);
        }
        row.appendChild(bubble); container.appendChild(row);
        row.style.animation = 'messageSlideIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) both';
        this.scrollToBottom(container);
        return row;
    }
    static hideThinking() {
        const row = document.getElementById('thinkingRow');
        if (row) { row.style.opacity = '0'; row.style.transform = 'scale(0.95)'; row.style.transition = 'opacity 0.2s, transform 0.2s'; setTimeout(() => row.remove(), 200); }
    }
    static hideWelcome() {
        const welcome = document.querySelector('.welcome-message');
        if (welcome) { welcome.style.opacity = '0'; welcome.style.transform = 'translateY(10px)'; welcome.style.transition = 'opacity 0.3s, transform 0.3s'; setTimeout(() => welcome.remove(), 300); }
    }
}


// ============================================================
// 聊天管理器类
// ============================================================
class ChatManager {
    constructor() {
        this.currentConvId = '';
        this.isProcessing = false;
        this.messagesContainer = document.getElementById('chatMessages');
        this.inputEl = document.getElementById('messageInput');
        this.btnSend = document.getElementById('btnSend');
        this.narrationTrack = document.getElementById('narrationTrack');
        this.narrationBar = document.getElementById('narrationBar');
        this.locationTag = document.getElementById('locationTag');
        this.welcomeEl = document.querySelector('.welcome-message');
        this._bindEvents();
    }

    _bindEvents() {
        this.btnSend.addEventListener('click', () => this.sendMessage());
        this.inputEl.addEventListener('keydown', (e) => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendMessage(); }
        });
        this.inputEl.addEventListener('input', () => {
            this.inputEl.style.height = 'auto';
            this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 120) + 'px';
        });
    }

    async sendMessage() {
        const message = this.inputEl.value.trim();
        if (!message || this.isProcessing) return;
        this.isProcessing = true; this.btnSend.disabled = true;
        this.inputEl.value = ''; this.inputEl.style.height = 'auto';
        if (this.welcomeEl) { AnimationEngine.hideWelcome(); this.welcomeEl = null; }

        const isPushCmd = message.startsWith('/推进');
        const displayMsg = isPushCmd ? (message.length > 3 ? message.slice(3).trim() || '（轻戳尤夏）' : '（轻戳尤夏）') : message;
        this._addBubble('user', displayMsg);
        AnimationEngine.showThinking(this.messagesContainer);

        try {
            const response = await authFetch('/api/chat', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conversation_id: this.currentConvId, message }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let aiBubble = null, fullText = '';
            AnimationEngine.hideThinking();
            aiBubble = this._addBubble('assistant', '');
            aiBubble.innerHTML = '';

            while (true) {
                const { done, value } = await reader.read();
                if (done) break;
                const chunk = decoder.decode(value, { stream: true });
                for (const line of chunk.split('\n')) {
                    if (!line.startsWith('data: ')) continue;
                    try {
                        const data = JSON.parse(line.slice(6));
                        if (data.type === 'conv_id') {
                            this.currentConvId = data.id;
                            if (window.emotionAssistant) window.emotionAssistant.onConvIdChange(data.id);
                        } else if (data.type === 'chunk') {
                            fullText += data.content;
                            aiBubble.textContent = fullText;
                            AnimationEngine.scrollToBottom(this.messagesContainer);
                        } else if (data.type === 'location') {
                            this.locationTag.textContent = data.content;
                        } else if (data.type === 'atmosphere') {
                            this._addNarrationCard(data.content);
                        } else if (data.type === 'story_time') {
                            if (window.emotionAssistant && window.emotionAssistant.eventPanel)
                                window.emotionAssistant.eventPanel.updateStoryTime(data.content);
                        } else if (data.type === 'story_summary') {
                            if (window.emotionAssistant && window.emotionAssistant.eventPanel)
                                window.emotionAssistant.eventPanel.updateSummary(data.content);
                        } else if (data.type === 'event_update') {
                            if (window.emotionAssistant && window.emotionAssistant.eventPanel)
                                window.emotionAssistant.eventPanel.renderEvents(data.events, data.has_push, data.push_hint);
                        }
                    } catch (e) { /* ignore */ }
                }
            }
            if (!fullText) aiBubble.textContent = 'No response.';
        } catch (error) {
            AnimationEngine.hideThinking();
            this._addBubble('assistant', `Error: ${error.message}`);
        }
        this.isProcessing = false; this.btnSend.disabled = false; this.inputEl.focus();
        if (window.emotionAssistant && window.emotionAssistant.historyManager)
            window.emotionAssistant.historyManager.loadHistory();
    }

    _addBubble(role, content) {
        const row = document.createElement('div');
        row.className = `message-row ${role}`;
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble';
        bubble.textContent = content;
        row.appendChild(bubble);
        this.messagesContainer.appendChild(row);
        AnimationEngine.slideInMessage(row);
        AnimationEngine.scrollToBottom(this.messagesContainer);
        return bubble;
    }

    async loadConversation(convId) {
        this.currentConvId = convId;
        this.messagesContainer.innerHTML = '';
        this.narrationTrack.innerHTML = '';
        this.narrationBar.style.display = 'none';
        try {
            const res = await authFetch(`/api/history/${convId}`);
            const data = await res.json();
            if (data.messages && data.messages.length > 0) {
                data.messages.forEach((msg) => this._addBubble(msg.role, msg.content));
            } else { this._showWelcome(); }
            if (data.atmosphere && data.atmosphere.length > 0) {
                data.atmosphere.forEach((text) => {
                    const display = text.replace(/【地点】\s*.+\n?/g, '').trim();
                    if (display) this._addNarrationCard(display);
                });
            }
            if (data.location) this.locationTag.textContent = data.location;
            if (window.emotionAssistant && window.emotionAssistant.eventPanel) {
                if (data.story_time) window.emotionAssistant.eventPanel.updateStoryTime(data.story_time);
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
        this.currentConvId = '';
        this.messagesContainer.innerHTML = '';
        this.narrationTrack.innerHTML = '';
        this.narrationBar.style.display = 'none';
        this.locationTag.textContent = '公寓客厅';
        this._showWelcome();
        if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.clear();
        this.inputEl.focus();
    }

    _addNarrationCard(text) {
        const card = document.createElement('div');
        card.className = 'narration-card'; card.textContent = text;
        this.narrationTrack.appendChild(card);
        this.narrationBar.style.display = 'block';
        this.narrationBar.scrollLeft = this.narrationBar.scrollWidth;
    }

    _showWelcome() {
        this.welcomeEl = document.createElement('div');
        this.welcomeEl.className = 'welcome-message';
        this.welcomeEl.innerHTML = `<svg class="welcome-icon" viewBox="0 0 64 64" width="56" height="56" fill="none" stroke="#6366f1" stroke-width="2.5" stroke-linecap="round">
            <path d="M32 8C19 8 8 17 8 28c0 10 8 17 16 19v9l10-8c12 0 22-9 22-20S45 8 32 8z"/>
            <circle cx="22" cy="28" r="2.5" fill="#6366f1"/><circle cx="42" cy="28" r="2.5" fill="#6366f1"/></svg>`;
        this.messagesContainer.appendChild(this.welcomeEl);
    }
}


// ============================================================
// 事件面板管理器类
// ============================================================
class EventPanel {
    constructor() {
        this.panel = document.getElementById('eventPanel');
        this.storyTimeEl = document.querySelector('#eventStoryTime .time-text');
        this.summaryEl = document.getElementById('eventSummary');
        this.eventList = document.getElementById('eventList');
        this.pushHint = document.getElementById('eventPushHint');
        this.pushHintText = document.getElementById('pushHintText');
        this.btnClose = document.getElementById('btnEventClose');
        this._bindEvents();
    }

    _bindEvents() { if (this.btnClose) this.btnClose.addEventListener('click', () => this.close()); }

    updateStoryTime(timeText) { if (this.storyTimeEl && timeText) this.storyTimeEl.textContent = timeText; }
    updateSummary(summary) { if (this.summaryEl && summary) this.summaryEl.textContent = summary; }

    showInitializing() {
        this.updateStoryTime('分析中...');
        this.summaryEl.textContent = '正在分析历史对话，生成事件节点...';
        this.eventList.innerHTML = '<div class="event-empty event-loading">AI 正在分析历史对话...</div>';
    }

    showEmpty() {
        this.eventList.innerHTML = '<div class="event-empty">暂无事件记录，开始对话吧</div>';
    }

    renderEvents(events, hasPush, pushHint) {
        if (!events || events.length === 0) { this.showEmpty(); return; }

        this.eventList.innerHTML = events.map((ev, i) => {
            const iconSvg = SVG_ICONS[ev.event_type] || SVG_ICONS.misc;
            const statusClass = `status-${ev.status}`;
            const randomClass = ev.event_type === 'random' ? ' event-random' : '';
            const timeLabel = ev.story_time ? ` · ${ev.story_time}` : '';
            return `<div class="event-card ${statusClass}${randomClass}" style="animation-delay: ${i * 0.05}s">
                <div class="event-card-header">
                    <span class="event-type-icon">${iconSvg}</span>
                    <span class="event-card-title">${this._escapeHtml(ev.title)}</span>
                    <span class="event-status-dot ${ev.status}"></span>
                </div>
                <div class="event-card-desc">${this._escapeHtml(ev.description)}</div>
                ${timeLabel ? `<div class="event-card-time">${timeLabel}</div>` : ''}
            </div>`;
        }).join('');

        if (hasPush && pushHint) { this.pushHint.style.display = 'block'; this.pushHintText.textContent = pushHint; }
        else if (pushHint) { this.pushHint.style.display = 'block'; this.pushHintText.textContent = pushHint; }
        else { this.pushHint.style.display = 'none'; }
    }

    clear() {
        this.updateStoryTime('第一天早晨');
        this.updateSummary('');
        this.showEmpty();
        this.pushHint.style.display = 'none';
    }

    toggle() { this.panel.classList.toggle('open'); }
    open() { this.panel.classList.add('open'); }
    close() { this.panel.classList.remove('open'); }

    _escapeHtml(str) {
        const div = document.createElement('div'); div.textContent = str; return div.innerHTML;
    }
}


// ============================================================
// 历史记录管理器类
// ============================================================
class HistoryManager {
    constructor(chatManager) {
        this.chatManager = chatManager;
        this.listEl = document.getElementById('historyList');
        this.btnNewChat = document.getElementById('btnNewChat');
        this._bindEvents();
    }
    _bindEvents() {
        this.btnNewChat.addEventListener('click', () => { this.chatManager.newConversation(); this.loadHistory(); });
        this.listEl.addEventListener('click', (e) => {
            const item = e.target.closest('.history-item');
            if (!item) return;
            const convId = item.dataset.id;
            if (e.target.closest('.btn-delete')) { e.stopPropagation(); this.deleteConversation(convId); return; }
            this._setActive(convId);
            this.chatManager.loadConversation(convId);
        });
    }
    async loadHistory() {
        try { const res = await authFetch('/api/history'); const conversations = await res.json(); this._render(conversations); }
        catch (e) { this.listEl.innerHTML = ''; }
    }
    _render(conversations) {
        if (!conversations || conversations.length === 0) { this.listEl.innerHTML = ''; return; }
        this.listEl.innerHTML = conversations.map((c, i) => `
            <li class="history-item ${c.id === this.chatManager.currentConvId ? 'active' : ''}" data-id="${c.id}" style="animation-delay: ${i * 0.04}s">
                <span class="conv-title">${this._escapeHtml(c.title)}</span>
                <span class="conv-date">${this._formatDate(c.updated_at)}</span>
                <button class="btn-delete" title="删除">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6"/><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                </button>
            </li>`).join('');
    }
    async deleteConversation(convId) {
        if (!confirm('Delete this conversation?')) return;
        try { await authFetch(`/api/history/${convId}`, { method: 'DELETE' }); if (convId === this.chatManager.currentConvId) this.chatManager.newConversation(); this.loadHistory(); }
        catch (e) { alert('Delete failed'); }
    }
    _setActive(convId) { this.listEl.querySelectorAll('.history-item').forEach((item) => item.classList.toggle('active', item.dataset.id === convId)); }
    _formatDate(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr), now = new Date(), diff = now - d;
        if (diff < 60000) return 'now';
        if (diff < 3600000) return Math.floor(diff / 60000) + 'm';
        if (diff < 86400000) return Math.floor(diff / 3600000) + 'h';
        return `${d.getMonth() + 1}/${d.getDate()}`;
    }
    _escapeHtml(str) { const div = document.createElement('div'); div.textContent = str; return div.innerHTML; }
}


// ============================================================
// UI 控制器类
// ============================================================
class UIController {
    constructor() {
        this.historyPanel = document.getElementById('historyPanel');
        this.overlay = document.getElementById('overlay');
        this.currentPanel = null;
        this._bindEvents();
    }
    _bindEvents() {
        document.querySelectorAll('.nav-btn').forEach((btn) => btn.addEventListener('click', () => this.togglePanel(btn.dataset.panel)));
        this.overlay.addEventListener('click', () => this.closeAllPanels());
        window.addEventListener('resize', () => { if (window.innerWidth > 1024) this.closeAllPanels(); });
        const charTitle = document.getElementById('charTitle');
        if (charTitle) {
            charTitle.style.pointerEvents = 'auto'; charTitle.style.cursor = 'pointer';
            charTitle.title = '点击切换故事进展面板';
            charTitle.addEventListener('click', () => {
                if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.toggle();
            });
        }
    }
    togglePanel(panelName) {
        if (panelName === 'events') { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.toggle(); return; }
        if (panelName !== 'history') return;
        if (this.currentPanel === 'history') { this.closeAllPanels(); return; }
        this.closeAllPanels();
        this.historyPanel.classList.add('open'); this.overlay.classList.add('show');
        this.currentPanel = 'history';
    }
    closeAllPanels() { this.historyPanel.classList.remove('open'); this.overlay.classList.remove('show'); this.currentPanel = null; }
}


// ============================================================
// 主控制器类
// ============================================================
class EmotionAssistant {
    constructor() {
        this.chatManager = new ChatManager();
        this.historyManager = new HistoryManager(this.chatManager);
        this.eventPanel = new EventPanel();
        this.uiController = new UIController();
        window.emotionAssistant = this;
        this._init();
    }
    _init() { this.historyManager.loadHistory(); document.getElementById('messageInput').focus(); }
    onConvIdChange(convId) { this.historyManager.loadHistory(); }
}


// ============================================================
// 全局实例 & 启动
// ============================================================
let auth;
let appInstance = null;

function onAuthSuccess() { if (!appInstance) appInstance = new EmotionAssistant(); }

document.addEventListener('DOMContentLoaded', async () => {
    auth = new AuthManager();
    const loggedIn = await auth.init();
    if (loggedIn) onAuthSuccess();
});