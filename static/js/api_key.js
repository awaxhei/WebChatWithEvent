/**
 * API Key 管理模块
 * 允许用户在 APK / PWA 中设置自己的 DeepSeek API Key
 * 存储在浏览器 localStorage 中，每次 API 请求时附带
 */
const API_KEY_STORAGE_KEY = 'yuxia_deepseek_api_key';

class ApiKeyManager {
    constructor() {
        this._key = null;
        this._load();
    }

    _load() {
        try {
            this._key = localStorage.getItem(API_KEY_STORAGE_KEY) || '';
        } catch {
            this._key = '';
        }
    }

    _save() {
        try {
            if (this._key) {
                localStorage.setItem(API_KEY_STORAGE_KEY, this._key);
            } else {
                localStorage.removeItem(API_KEY_STORAGE_KEY);
            }
        } catch {}
    }

    /** 获取当前 API Key */
    get() {
        return this._key;
    }

    /** 设置 API Key */
    set(key) {
        this._key = (key || '').trim();
        this._save();
        return this._key;
    }

    /** 是否已设置 */
    isSet() {
        return !!this._key;
    }

    /** 清除 */
    clear() {
        this._key = '';
        this._save();
    }

    /** 获取请求头 */
    getHeaders() {
        if (this._key) {
            return { 'X-DeepSeek-API-Key': this._key };
        }
        return {};
    }
}

// 全局单例
window.apiKeyManager = new ApiKeyManager();