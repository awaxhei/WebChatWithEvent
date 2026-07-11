/**
 * AI 情感助手 - 前端类架构 (双 AI)
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
            if (valid) {
                this._hideOverlay();
                return true;
            }
            localStorage.removeItem('auth_token');
            this.token = null;
        }
        this._showOverlay();
        return false;
    }

    async _verifyToken() {
        try {
            const res = await fetch('/api/auth/verify', {
                headers: { 'Authorization': `Bearer ${this.token}` }
            });
            const data = await res.json();
            return data.valid === true;
        } catch {
            return false;
        }
    }

    async _handleLogin() {
        const password = this.passwordInput.value;
        const remember = this.rememberCheck.checked;
        if (!password) {
            this._showError('请输入密码');
            return;
        }
        this.btnLogin.disabled = true;
        this.errorEl.textContent = '';
        try {
            const res = await fetch('/api/auth/login', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password, remember })
            });
            const data = await res.json();
            if (data.success) {
                this.token = data.token;
                if (remember) {
                    localStorage.setItem('auth_token', data.token);
                } else {
                    localStorage.removeItem('auth_token');
                }
                this._hideOverlay();
                onAuthSuccess();
            } else {
                this._showError(data.error || '密码错误');
                this.passwordInput.value = '';
                this.passwordInput.focus();
            }
        } catch {
            this._showError('网络错误');
        }
        this.btnLogin.disabled = false;
    }

    _showError(msg) {
        this.errorEl.textContent = msg;
        this.errorEl.style.animation = 'none';
        this.errorEl.offsetHeight;
        this.errorEl.style.animation = 'fadeIn 0.3s ease-out';
        setTimeout(() => { this.errorEl.textContent = ''; }, 3000);
    }

    _showOverlay() {
        this.overlay.classList.remove('hidden');
        this.passwordInput.focus();
    }

    _hideOverlay() {
        this.overlay.classList.add('hidden');
    }

    getAuthHeaders() {
        return this.token ? { 'Authorization': `Bearer ${this.token}` } : {};
    }
}

// ============================================================
// 认证 HTTP 助手
// ============================================================
const authFetch = (url, options = {}) => {
    const headers = { ...options.headers, ...auth.getAuthHeaders() };
    return fetch(url, { ...options, headers });
};

// ============================================================
// 动画引擎类
// ============================================================
class AnimationEngine {
    static slideInMessage(element, delay = 0) {
        element.style.animation = 'none';
        element.offsetHeight;
        element.style.animation = `messageSlideIn 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) ${delay}ms both`;
    }

    static scrollToBottom(container) {
        container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
    }

    static showThinking(container) {
        const row = document.createElement('div');
        row.className = 'message-row assistant';
        row.id = 'thinkingRow';
        const bubble = document.createElement('div');
        bubble.className = 'message-bubble thinking-indicator';
        for (let i = 0; i < 3; i++) {
            const dot = document.createElement('span');
            dot.className = 'thinking-dot';
            bubble.appendChild(dot);
        }
        row.appendChild(bubble);
        container.appendChild(row);
        row.style.animation = 'messageSlideIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) both';
        this.scrollToBottom(container);
        return row;
    }

    static hideThinking() {
        const row = document.getElementById('thinkingRow');
        if (row) {
            row.style.opacity = '0';
            row.style.transform = 'scale(0.95)';
            row.style.transition = 'opacity 0.2s, transform 0.2s';
            setTimeout(() => row.remove(), 200);
        }
    }

    static hideWelcome() {
        const welcome = document.querySelector('.welcome-message');
        if (welcome) {
            welcome.style.opacity = '0';
            welcome.style.transform = 'translateY(10px)';
            welcome.style.transition = 'opacity 0.3s, transform 0.3s';
            setTimeout(() => welcome.remove(), 300);
        }
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
            if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault();
                this.sendMessage();
            }
        });
        this.inputEl.addEventListener('input', () => {
            this.inputEl.style.height = 'auto';
            this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 120) + 'px';
        });
    }

    async sendMessage() {
        const message = this.inputEl.value.trim();
        if (!message || this.isProcessing) return;

        this.isProcessing = true;
        this.btnSend.disabled = true;
        this.inputEl.value = '';
        this.inputEl.style.height = 'auto';

        if (this.welcomeEl) {
            AnimationEngine.hideWelcome();
            this.welcomeEl = null;
        }

        this._addBubble('user', message);
        AnimationEngine.showThinking(this.messagesContainer);

        try {
            const response = await authFetch('/api/chat', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ conversation_id: this.currentConvId, message }),
            });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let aiBubble = null;
            let fullText = '';

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
                            if (window.emotionAssistant) {
                                window.emotionAssistant.onConvIdChange(data.id);
                            }
                        } else if (data.type === 'chunk') {
                            fullText += data.content;
                            aiBubble.textContent = fullText;
                            AnimationEngine.scrollToBottom(this.messagesContainer);
                        } else if (data.type === 'location') {
                            this.locationTag.textContent = data.content;
                        } else if (data.type === 'atmosphere') {
                            this._addNarrationCard(data.content);
                        }
                    } catch (e) { /* ignore parse errors */ }
                }
            }

            if (!fullText) aiBubble.textContent = 'No response. Check Ollama.';

        } catch (error) {
            AnimationEngine.hideThinking();
            this._addBubble('assistant', `Error: ${error.message}`);
        }

        this.isProcessing = false;
        this.btnSend.disabled = false;
        this.inputEl.focus();

        if (window.emotionAssistant && window.emotionAssistant.historyManager) {
            window.emotionAssistant.historyManager.loadHistory();
        }
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
            } else {
                this._showWelcome();
            }
            if (data.atmosphere && data.atmosphere.length > 0) {
                data.atmosphere.forEach((text) => {
                    // 从存储文本中去除【地点】标签后显示
                    const display = text.replace(/【地点】\s*.+\n?/g, '').trim();
                    if (display) this._addNarrationCard(display);
                });
            }
            if (data.location) {
                this.locationTag.textContent = data.location;
            }
        } catch (e) {
            this._showWelcome();
        }
        AnimationEngine.scrollToBottom(this.messagesContainer);
    }

    newConversation() {
        this.currentConvId = '';
        this.messagesContainer.innerHTML = '';
        this.narrationTrack.innerHTML = '';
        this.narrationBar.style.display = 'none';
        this.locationTag.textContent = '公寓客厅';
        this._showWelcome();
        this.inputEl.focus();
    }

    _addNarrationCard(text) {
        const card = document.createElement('div');
        card.className = 'narration-card';
        card.textContent = text;
        this.narrationTrack.appendChild(card);
        this.narrationBar.style.display = 'block';
        // 自动滚动到最右侧
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
        this.btnNewChat.addEventListener('click', () => {
            this.chatManager.newConversation();
            this.loadHistory();
        });
        this.listEl.addEventListener('click', (e) => {
            const item = e.target.closest('.history-item');
            if (!item) return;
            const convId = item.dataset.id;
            if (e.target.closest('.btn-delete')) {
                e.stopPropagation();
                this.deleteConversation(convId);
                return;
            }
            this._setActive(convId);
            this.chatManager.loadConversation(convId);
        });
    }

    async loadHistory() {
        try {
            const res = await authFetch('/api/history');
            const conversations = await res.json();
            this._render(conversations);
        } catch (e) {
            this.listEl.innerHTML = '';
        }
    }

    _render(conversations) {
        if (!conversations || conversations.length === 0) {
            this.listEl.innerHTML = '';
            return;
        }
        this.listEl.innerHTML = conversations.map((c, i) => `
            <li class="history-item ${c.id === this.chatManager.currentConvId ? 'active' : ''}"
                data-id="${c.id}" style="animation-delay: ${i * 0.04}s">
                <span class="conv-title">${this._escapeHtml(c.title)}</span>
                <span class="conv-date">${this._formatDate(c.updated_at)}</span>
                <button class="btn-delete" title="删除">
                    <svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="2">
                        <polyline points="3 6 5 6 21 6"/>
                        <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
                    </svg>
                </button>
            </li>`).join('');
    }

    async deleteConversation(convId) {
        if (!confirm('Delete this conversation?')) return;
        try {
            await authFetch(`/api/history/${convId}`, { method: 'DELETE' });
            if (convId === this.chatManager.currentConvId) {
                this.chatManager.newConversation();
            }
            this.loadHistory();
        } catch (e) {
            alert('Delete failed');
        }
    }

    _setActive(convId) {
        this.listEl.querySelectorAll('.history-item').forEach((item) => {
            item.classList.toggle('active', item.dataset.id === convId);
        });
    }

    _formatDate(dateStr) {
        if (!dateStr) return '';
        const d = new Date(dateStr);
        const now = new Date();
        const diff = now - d;
        if (diff < 60000) return 'now';
        if (diff < 3600000) return Math.floor(diff / 60000) + 'm';
        if (diff < 86400000) return Math.floor(diff / 3600000) + 'h';
        return `${d.getMonth() + 1}/${d.getDate()}`;
    }

    _escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }
}


// ============================================================
// UI 控制器类 - 响应式面板管理
// ============================================================
class UIController {
    constructor() {
        this.historyPanel = document.getElementById('historyPanel');
        this.overlay = document.getElementById('overlay');
        this.currentPanel = null;
        this._bindEvents();
    }

    _bindEvents() {
        document.querySelectorAll('.nav-btn').forEach((btn) => {
            btn.addEventListener('click', () => {
                this.togglePanel(btn.dataset.panel);
            });
        });
        this.overlay.addEventListener('click', () => this.closeAllPanels());
        window.addEventListener('resize', () => {
            if (window.innerWidth > 768) this.closeAllPanels();
        });
    }

    togglePanel(panelName) {
        if (panelName !== 'history') return;
        if (this.currentPanel === 'history') {
            this.closeAllPanels();
            return;
        }
        this.closeAllPanels();
        this.historyPanel.classList.add('open');
        this.overlay.classList.add('show');
        this.currentPanel = 'history';
    }

    closeAllPanels() {
        this.historyPanel.classList.remove('open');
        this.overlay.classList.remove('show');
        this.currentPanel = null;
    }
}


// ============================================================
// 主控制器类
// ============================================================
class EmotionAssistant {
    constructor() {
        this.chatManager = new ChatManager();
        this.historyManager = new HistoryManager(this.chatManager);
        this.uiController = new UIController();
        window.emotionAssistant = this;
        this._init();
    }

    _init() {
        this.historyManager.loadHistory();
        document.getElementById('messageInput').focus();
    }

    onConvIdChange(convId) {
        this.historyManager.loadHistory();
    }
}


// ============================================================
// 全局实例 & 启动
// ============================================================
let auth;
let appInstance = null;

function onAuthSuccess() {
    if (!appInstance) {
        appInstance = new EmotionAssistant();
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    auth = new AuthManager();
    const loggedIn = await auth.init();
    if (loggedIn) {
        onAuthSuccess();
    }
});
