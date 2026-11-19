"""
EmbeddingClient: 文本转向量
- 开发环境：优先尝试 Ollama（nomic-embed-text）
- APK/生产环境：本地 sentence-transformers（BAAI/bge-large-zh-v1.5）
兜底：无嵌入时返回 None，RAG 自动降级跳过
"""
import json


def _requests():
    """延迟导入：默认 EMBEDDING_ENABLED=False 时根本用不到，别拖慢启动"""
    import requests
    return requests


class EmbeddingClient:
    def __init__(self, api_key=None):
        # 尝试初始化本地模型
        self._local_model = None
        self._local_failed = False
        self._local_model_name = "BAAI/bge-large-zh-v1.5"
        self._ollama_url = "http://localhost:11434/api/embeddings"
        self._ollama_model = "nomic-embed-text"
        self._prefer_ollama = self._check_ollama()

    def _check_ollama(self):
        """检测 Ollama 是否可用"""
        try:
            resp = _requests().get("http://localhost:11434/api/tags", timeout=3)
            return resp.status_code == 200
        except Exception:
            return False

    def _get_local_model(self):
        """惰性加载本地 sentence-transformers 模型（失败一次后不再重试）"""
        if self._local_model is not None:
            return self._local_model
        if self._local_failed:
            return None
        try:
            from sentence_transformers import SentenceTransformer
            self._local_model = SentenceTransformer(self._local_model_name)
            print(f"[Embedding] 本地模型已加载: {self._local_model_name}")
            return self._local_model
        except ImportError:
            self._local_failed = True
            print("[Embedding] 未安装 sentence-transformers，语义检索降级为词面相似度")
            return None
        except Exception as e:
            self._local_failed = True
            print(f"[Embedding] 本地模型加载失败: {e}")
            return None

    def embed(self, text):
        """单条文本 → 向量"""
        if self._prefer_ollama:
            result = self._embed_ollama(text)
            if result is not None:
                return result
            # Ollama 不可用，fall through 到本地模型
        return self._embed_local(text)

    def embed_batch(self, texts):
        """批量文本 → 向量列表"""
        if self._prefer_ollama:
            results = self._embed_ollama_batch(texts)
            if results:
                return results
        return self._embed_local_batch(texts)

    def _embed_ollama(self, text):
        try:
            resp = _requests().post(
                self._ollama_url,
                json={"model": self._ollama_model, "prompt": text},
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json().get("embedding", None)
        except Exception as e:
            print(f"[Embedding Ollama] 失败: {e}")
            self._prefer_ollama = False
            return None

    def _embed_ollama_batch(self, texts):
        results = []
        for t in texts:
            results.append(self._embed_ollama(t))
        return results

    def _embed_local(self, text):
        model = self._get_local_model()
        if model is None:
            return None
        try:
            vec = model.encode(text, normalize_embeddings=True)
            return vec.tolist()
        except Exception as e:
            print(f"[Embedding Local] 失败: {e}")
            return None

    def _embed_local_batch(self, texts):
        model = self._get_local_model()
        if model is None:
            return [None] * len(texts)
        try:
            vecs = model.encode(texts, normalize_embeddings=True)
            return [v.tolist() for v in vecs]
        except Exception as e:
            print(f"[Embedding Local Batch] 失败: {e}")
            return [None] * len(texts)