/**
 * AI 情感助手 - 前端类架构 (三 AI + 账号系统)
 */
class AuthManager {
    constructor() {
        this.overlay = document.getElementById('loginOverlay');
        this.usernameInput = document.getElementById('loginUsername');
        this.passwordInput = document.getElementById('loginPassword');
        this.rememberCheck = document.getElementById('rememberPwd');
        this.btnLogin = document.getElementById('btnLogin');
        this.errorEl = document.getElementById('loginError');
        this._bindEvents();
    }
    _bindEvents() {
        this.btnLogin.addEventListener('click', () => this._handleLogin());
        this.passwordInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') this._handleLogin(); });
        this.usernameInput.addEventListener('keydown', (e) => { if (e.key === 'Enter') this.passwordInput.focus(); });
    }
    async init() {
        const valid = await this._verifyToken();
        if (valid) { this._hideOverlay(); return true; }
        this._showOverlay(); return false;
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
            if (data.success) { this._hideOverlay(); onAuthSuccess(); }
            else { this._showError(data.error || '登录失败'); this.passwordInput.value = ''; this.passwordInput.focus(); }
        } catch { this._showError('网络错误'); }
        this.btnLogin.disabled = false;
    }
    _showError(msg) { this.errorEl.textContent = msg; this.errorEl.style.animation = 'none'; this.errorEl.offsetHeight; this.errorEl.style.animation = 'fadeIn 0.3s ease-out'; setTimeout(() => { this.errorEl.textContent = ''; }, 3000); }
    _showOverlay() { this.overlay.classList.remove('hidden'); this.usernameInput.focus(); }
    _hideOverlay() { this.overlay.classList.add('hidden'); }
}
const authFetch = (url, options = {}) => fetch(url, { ...options, credentials: 'include' });

const SVG_ICONS = {
    milestone: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6366f1" stroke-width="1.5"><polygon points="8 1.5 10 6 15 6 11.2 9 12.5 13.5 8 11 3.5 13.5 4.8 9 1 6 6 6"/></svg>',
    scene_change: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><path d="M3 8h10M8 3l5 5-5 5"/></svg>',
    emotional_shift: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><circle cx="5" cy="7" r="1"/><circle cx="11" cy="7" r="1"/><path d="M3 11c1.5 2 5.5 2 10 0"/></svg>',
    time_advance: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#6b7280" stroke-width="1.5"><circle cx="8" cy="8" r="6.5"/><polyline points="8 5 8 8 10.5 10.5"/></svg>',
    random: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#f59e0b" stroke-width="1.5"><rect x="2.5" y="2.5" width="11" height="11" rx="3"/><circle cx="5.5" cy="5.5" r="1.2"/><circle cx="10.5" cy="5.5" r="1.2"/><circle cx="8" cy="9" r="1"/></svg>',
    misc: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="#a0aec0" stroke-width="1.5"><circle cx="8" cy="8" r="2"/><path d="M8 1v2m0 10v2M1 8h2m10 0h2"/></svg>',
};

class AnimationEngine {
    static slideInMessage(el, delay = 0) { el.style.animation = 'none'; el.offsetHeight; el.style.animation = `messageSlideIn 0.35s cubic-bezier(0.34, 1.56, 0.64, 1) ${delay}ms both`; }
    static scrollToBottom(container) { container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' }); }
    static showThinking(container) { const row = document.createElement('div'); row.className = 'message-row assistant'; row.id = 'thinkingRow'; const bubble = document.createElement('div'); bubble.className = 'message-bubble thinking-indicator'; for (let i = 0; i < 3; i++) { const dot = document.createElement('span'); dot.className = 'thinking-dot'; bubble.appendChild(dot); } row.appendChild(bubble); container.appendChild(row); row.style.animation = 'messageSlideIn 0.3s cubic-bezier(0.34, 1.56, 0.64, 1) both'; this.scrollToBottom(container); return row; }
    static hideThinking() { const row = document.getElementById('thinkingRow'); if (row) { row.style.opacity = '0'; row.style.transform = 'scale(0.95)'; row.style.transition = 'opacity 0.2s, transform 0.2s'; setTimeout(() => row.remove(), 200); } }
    static hideWelcome() { const w = document.querySelector('.welcome-message'); if (w) { w.style.opacity = '0'; w.style.transform = 'translateY(10px)'; w.style.transition = 'opacity 0.3s, transform 0.3s'; setTimeout(() => w.remove(), 300); } }
}

class ChatManager {
    constructor() {
        this.currentConvId = ''; this.isProcessing = false; this.pushMode = false;
        this.messagesContainer = document.getElementById('chatMessages');
        this.inputEl = document.getElementById('messageInput');
        this.btnSend = document.getElementById('btnSend');
        this.btnPushMode = document.getElementById('btnPushMode');
        this.narrationTrack = document.getElementById('narrationTrack');
        this.narrationBar = document.getElementById('narrationBar');
        this.locationTag = document.getElementById('locationTag');
        this.welcomeEl = document.querySelector('.welcome-message');
        this._bindEvents();
    }
    _bindEvents() {
        this.btnSend.addEventListener('click', () => this.sendMessage());
        this.inputEl.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); this.sendMessage(); } });
        this.inputEl.addEventListener('input', () => { this.inputEl.style.height = 'auto'; this.inputEl.style.height = Math.min(this.inputEl.scrollHeight, 120) + 'px'; });
        this.btnPushMode.addEventListener('click', () => this.togglePushMode());
    }
    togglePushMode() { this.pushMode = !this.pushMode; if (this.pushMode) { this.btnPushMode.classList.add('active'); this.btnSend.classList.add('push-active'); this.inputEl.placeholder = '输入推进方向...'; } else { this.btnPushMode.classList.remove('active'); this.btnSend.classList.remove('push-active'); this.inputEl.placeholder = '输入消息...'; } }
    _disableInput() { this.inputEl.disabled = true; this.btnSend.disabled = true; this.btnPushMode.disabled = true; }
    _enableInput() { this.inputEl.disabled = false; this.btnSend.disabled = false; this.btnPushMode.disabled = false; this.inputEl.focus(); }
    async sendMessage() {
        const message = this.inputEl.value.trim();
        if (!message || this.isProcessing) return;
        this.isProcessing = true; this._disableInput();
        this.inputEl.value = ''; this.inputEl.style.height = 'auto';
        if (this.welcomeEl) { AnimationEngine.hideWelcome(); this.welcomeEl = null; }
        const displayMsg = this.pushMode ? (message || '（轻戳尤夏）') : message;
        const serverMsg = this.pushMode ? `/推进 ${message}` : message;
        if (this.pushMode) this.togglePushMode();
        this._addBubble('user', displayMsg);
        AnimationEngine.showThinking(this.messagesContainer);
        try {
            const response = await authFetch('/api/chat', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ conversation_id: this.currentConvId, message: serverMsg }) });
            if (!response.ok) throw new Error(`HTTP ${response.status}`);
            const reader = response.body.getReader(); const decoder = new TextDecoder();
            let aiBubble = null, fullText = '';
            AnimationEngine.hideThinking();
            aiBubble = this._addBubble('assistant', ''); aiBubble.innerHTML = '';
            while (true) { const { done, value } = await reader.read(); if (done) break; for (const line of decoder.decode(value, { stream: true }).split('\n')) { if (!line.startsWith('data: ')) continue; try { const data = JSON.parse(line.slice(6)); if (data.type === 'conv_id') { this.currentConvId = data.id; if (window.emotionAssistant) window.emotionAssistant.onConvIdChange(data.id); } else if (data.type === 'chunk') { fullText += data.content; aiBubble.textContent = fullText; AnimationEngine.scrollToBottom(this.messagesContainer); } else if (data.type === 'location') { this.locationTag.textContent = data.content; } else if (data.type === 'atmosphere') { this._addNarrationCard(data.content); } else if (data.type === 'event_organizing') { this._addEventOrganizing(); } else if (data.type === 'world_narration') { this._addWorldNarration(data.content, data.intensity || 'low'); } else if (data.type === 'story_time') { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.updateStoryTime(data.content); } else if (data.type === 'story_summary') { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.updateSummary(data.content); } else if (data.type === 'event_update') { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.renderEvents(data.events, data.has_push, data.push_hint); } } catch (e) {} } }
            if (!fullText) aiBubble.textContent = 'No response.';
        } catch (error) { AnimationEngine.hideThinking(); this._addBubble('assistant', `Error: ${error.message}`); }
        this.isProcessing = false; this._enableInput();
        if (window.emotionAssistant && window.emotionAssistant.historyManager) window.emotionAssistant.historyManager.loadHistory();
    }
    _addBubble(role, content) { const row = document.createElement('div'); row.className = `message-row ${role}`; const bubble = document.createElement('div'); bubble.className = 'message-bubble'; bubble.textContent = content; row.appendChild(bubble); this.messagesContainer.appendChild(row); AnimationEngine.slideInMessage(row); AnimationEngine.scrollToBottom(this.messagesContainer); return bubble; }
    _addEventOrganizing() { const wrapper = document.createElement('div'); wrapper.className = 'world-narration'; wrapper.innerHTML = '<div class="world-narration-inner"><div class="world-narration-label">事件组织中...</div></div>'; wrapper.id = 'eventOrganizingHint'; this.messagesContainer.appendChild(wrapper); AnimationEngine.scrollToBottom(this.messagesContainer); }
    _addWorldNarration(text, intensity) { const hint = document.getElementById('eventOrganizingHint'); if (hint) hint.remove(); const wrapper = document.createElement('div'); wrapper.className = `world-narration ${intensity === 'medium' ? 'medium' : ''}`; wrapper.innerHTML = `<div class="world-narration-inner"><div class="world-narration-label">— 世界 —</div><div class="world-narration-text">${text}</div></div>`; this.messagesContainer.appendChild(wrapper); AnimationEngine.scrollToBottom(this.messagesContainer); }
    async loadConversation(convId) { this.currentConvId = convId; this.messagesContainer.innerHTML = ''; this.narrationTrack.innerHTML = ''; this.narrationBar.style.display = 'none'; try { const res = await authFetch(`/api/history/${convId}`); const data = await res.json(); if (data.messages && data.messages.length > 0) { data.messages.forEach((msg) => this._addBubble(msg.role, msg.content)); } else { this._showWelcome(); } if (data.atmosphere && data.atmosphere.length > 0) { data.atmosphere.forEach((text) => { const d = text.replace(/【地点】\s*.+\n?/g, '').trim(); if (d) this._addNarrationCard(d); }); } if (data.location) this.locationTag.textContent = data.location; if (window.emotionAssistant && window.emotionAssistant.eventPanel) { if (data.story_time) window.emotionAssistant.eventPanel.updateStoryTime(data.story_time); if (data.story_summary) window.emotionAssistant.eventPanel.updateSummary(data.story_summary); if (data.events && data.events.length > 0) { window.emotionAssistant.eventPanel.renderEvents(data.events, false, ''); } else if (data.messages && data.messages.length > 0 && !data.events_initialized) { window.emotionAssistant.eventPanel.showInitializing(); this._initEventsForConversation(convId); } } } catch (e) { this._showWelcome(); } AnimationEngine.scrollToBottom(this.messagesContainer); }
    async _initEventsForConversation(convId) { try { const res = await authFetch(`/api/events/init/${convId}`, { method: 'POST' }); const data = await res.json(); if (data.success && window.emotionAssistant && window.emotionAssistant.eventPanel) { if (data.story_time) window.emotionAssistant.eventPanel.updateStoryTime(data.story_time); if (data.story_summary) window.emotionAssistant.eventPanel.updateSummary(data.story_summary); if (data.events) window.emotionAssistant.eventPanel.renderEvents(data.events, data.has_push, data.push_hint); } else { window.emotionAssistant.eventPanel.showEmpty(); } } catch (e) { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.showEmpty(); } }
    newConversation() { this.currentConvId = ''; this.messagesContainer.innerHTML = ''; this.narrationTrack.innerHTML = ''; this.narrationBar.style.display = 'none'; this.locationTag.textContent = '公寓客厅'; this._showWelcome(); if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.clear(); this.inputEl.focus(); }
    _addNarrationCard(text) { this.narrationTrack.innerHTML = ''; const card = document.createElement('div'); card.className = 'narration-card'; card.textContent = text; this.narrationTrack.appendChild(card); this.narrationBar.style.display = 'block'; }
    _showWelcome() { this.welcomeEl = document.createElement('div'); this.welcomeEl.className = 'welcome-message'; this.welcomeEl.innerHTML = `<svg class="welcome-icon" viewBox="0 0 64 64" width="56" height="56" fill="none" stroke="#6366f1" stroke-width="2.5" stroke-linecap="round"><path d="M32 8C19 8 8 17 8 28c0 10 8 17 16 19v9l10-8c12 0 22-9 22-20S45 8 32 8z"/><circle cx="22" cy="28" r="2.5" fill="#6366f1"/><circle cx="42" cy="28" r="2.5" fill="#6366f1"/></svg>`; this.messagesContainer.appendChild(this.welcomeEl); }
}

class EventPanel {
    constructor() { this.panel = document.getElementById('eventPanel'); this.storyTimeEl = document.querySelector('#eventStoryTime .time-text'); this.summaryEl = document.getElementById('eventSummary'); this.eventList = document.getElementById('eventList'); this.pushHint = document.getElementById('eventPushHint'); this.pushHintText = document.getElementById('pushHintText'); this.btnClose = document.getElementById('btnEventClose'); if (this.btnClose) this.btnClose.addEventListener('click', () => this.close()); }
    updateStoryTime(t) { if (this.storyTimeEl && t) this.storyTimeEl.textContent = t; }
    updateSummary(s) { if (this.summaryEl && s) this.summaryEl.textContent = s; }
    showInitializing() { this.updateStoryTime('分析中...'); this.summaryEl.textContent = '正在分析历史对话...'; this.eventList.innerHTML = '<div class="event-empty event-loading">AI 正在分析历史对话...</div>'; }
    showEmpty() { this.eventList.innerHTML = '<div class="event-empty">暂无事件记录，开始对话吧</div>'; }
    _ensureVisible() { this.panel.classList.remove('collapsed'); this.panel.offsetHeight; if (window.innerWidth <= 768) this.panel.classList.add('open'); }
    renderEvents(events, hasPush, pushHint) {
        if (!events || events.length === 0) return;
        this._ensureVisible();
        // 按"天"分组
        const groups = this._groupByDay(events);
        const dayKeys = Object.keys(groups);
        if (dayKeys.length === 0) return;
        const latestDay = dayKeys[dayKeys.length - 1];
        let html = '';
        let needsExpand = dayKeys.length > 2;
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
        // 50+ events 时显示"查看全部"按钮（兜底）
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
    clear() { this.updateStoryTime('第一天早晨'); this.updateSummary(''); this.showEmpty(); this.pushHint.style.display = 'none'; }
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
    constructor() { this.historyPanel = document.getElementById('historyPanel'); this.overlay = document.getElementById('overlay'); this.currentPanel = null; this._bindEvents(); }
    _bindEvents() { document.querySelectorAll('.nav-btn').forEach((btn) => btn.addEventListener('click', () => this.togglePanel(btn.dataset.panel))); this.overlay.addEventListener('click', () => this.closeAllPanels()); window.addEventListener('resize', () => { if (window.innerWidth > 768) this.closeAllPanels(); }); const charTitle = document.getElementById('charTitle'); if (charTitle) { charTitle.style.pointerEvents = 'auto'; charTitle.style.cursor = 'pointer'; charTitle.title = '点击切换故事面板'; charTitle.addEventListener('click', () => { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.toggle(); }); } const btnLogout = document.getElementById('btnLogout'); if (btnLogout) btnLogout.addEventListener('click', async () => { await authFetch('/api/auth/logout', { method: 'POST' }); location.reload(); }); }
    togglePanel(panelName) { if (panelName === 'events') { if (window.emotionAssistant && window.emotionAssistant.eventPanel) window.emotionAssistant.eventPanel.toggle(); return; } if (panelName !== 'history') return; if (this.currentPanel === 'history') { this.closeAllPanels(); return; } this.closeAllPanels(); this.historyPanel.classList.add('open'); this.overlay.classList.add('show'); this.currentPanel = 'history'; }
    closeAllPanels() { this.historyPanel.classList.remove('open'); this.overlay.classList.remove('show'); this.currentPanel = null; }
}

class EmotionAssistant {
    constructor() { this.chatManager = new ChatManager(); this.historyManager = new HistoryManager(this.chatManager); this.eventPanel = new EventPanel(); this.uiController = new UIController(); window.emotionAssistant = this; this._init(); }
    _init() { this.historyManager.loadHistory(); document.getElementById('messageInput').focus(); }
    onConvIdChange(convId) { this.historyManager.loadHistory(); }
}

let auth, appInstance = null;
function onAuthSuccess() { if (!appInstance) appInstance = new EmotionAssistant(); }
document.addEventListener('DOMContentLoaded', async () => { auth = new AuthManager(); if (await auth.init()) onAuthSuccess(); });