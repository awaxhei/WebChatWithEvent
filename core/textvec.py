"""
文本向量工具 —— 统一的 embedding 入口与相似度计算

- 有 embedding 能力（Ollama / 本地 sentence-transformers）时用余弦相似度
- 没有时自动降级为中文二元组（bigram）词面相似度，保证功能不瘫
"""
import array
import math
import re

from config import EMBEDDING_ENABLED
from core.embedding_client import EmbeddingClient

_state = {"client": None, "ok": None}

_PUNCT_RE = re.compile(r"[\s\W_]+", re.UNICODE)


def client():
    if _state["client"] is None:
        _state["client"] = EmbeddingClient()
    return _state["client"]


def available():
    """embedding 是否可用。

    默认（EMBEDDING_ENABLED=False）直接返回 False，**不做任何网络探测**：
    之前每次首轮都会去连 Ollama（3 秒超时）再试本地模型，白白拖慢首条消息。
    """
    if not EMBEDDING_ENABLED:
        return False
    if _state["ok"] is None:
        try:
            c = client()
            if c._prefer_ollama:
                _state["ok"] = True
            else:
                import importlib.util
                _state["ok"] = importlib.util.find_spec("sentence_transformers") is not None
        except Exception:
            _state["ok"] = False
    return bool(_state["ok"])


def embed(text):
    """单条文本向量；不可用时返回 None"""
    if not text or not available():
        return None
    try:
        return client().embed(text)
    except Exception:
        return None


def to_blob(vec):
    """向量 → SQLite BLOB"""
    if not vec:
        return None
    return array.array("f", vec).tobytes()


def from_blob(blob):
    """SQLite BLOB → 向量列表"""
    if not blob:
        return None
    a = array.array("f")
    a.frombytes(blob)
    return a.tolist()


def cosine(a, b):
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _bigrams(text):
    """中文友好：去掉标点后取相邻二字组合"""
    s = _PUNCT_RE.sub("", (text or "").lower())
    if len(s) < 2:
        return {s} if s else set()
    return {s[i:i + 2] for i in range(len(s) - 1)}


def lexical_similarity(a, b):
    """无 embedding 时的词面相似度。

    用"查询覆盖率 + Jaccard"混合：短查询（如"煎饺"）只要被记忆完整覆盖就能拿高分，
    纯 Jaccard 会因为它相对长文本太短而被稀释到接近 0。
    """
    ga, gb = _bigrams(a), _bigrams(b)
    if not ga or not gb:
        return 0.0
    inter = len(ga & gb)
    if not inter:
        return 0.0
    coverage = inter / len(ga)
    jaccard = inter / len(ga | gb)
    return 0.65 * coverage + 0.35 * jaccard


def similarity(a_text, a_vec, b_text, b_vec):
    """优先向量余弦，其次词面相似度"""
    if a_vec and b_vec:
        return max(0.0, cosine(a_vec, b_vec))
    return lexical_similarity(a_text, b_text)
