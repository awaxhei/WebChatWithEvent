"""
VectorStore: ChromaDB 向量存储封装
提供消息 embedding 的存储、检索、删除功能
按 conversation_id 隔离 collection
"""
import os
import chromadb
from chromadb.config import Settings


VECTOR_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "chromadb")


class VectorStore:
    def __init__(self, path=VECTOR_DB_PATH):
        os.makedirs(path, exist_ok=True)
        self._client = chromadb.PersistentClient(path=path, settings=Settings(anonymized_telemetry=False))
        self._collections = {}

    def _get_collection(self, conv_id):
        """获取或创建 conversation 对应的 collection"""
        col_name = f"conv_{conv_id}"
        if conv_id not in self._collections:
            try:
                self._collections[conv_id] = self._client.get_collection(name=col_name)
            except Exception:
                self._collections[conv_id] = self._client.create_collection(name=col_name)
        return self._collections[conv_id]

    def add_message(self, conv_id, msg_id, role, content, embedding):
        """添加一条消息的向量记录"""
        if embedding is None:
            return
        try:
            col = self._get_collection(conv_id)
            col.add(
                documents=[content],
                embeddings=[embedding],
                metadatas=[{"role": role, "msg_id": msg_id}],
                ids=[f"msg_{msg_id}"],
            )
        except Exception as e:
            print(f"[VectorStore] 添加消息失败: {e}")

    def search_similar(self, conv_id, query_embedding, top_k=5):
        """检索最相似的 top_k 条历史消息"""
        if query_embedding is None:
            return []
        try:
            col = self._get_collection(conv_id)
            results = col.query(
                query_embeddings=[query_embedding],
                n_results=min(top_k, col.count()),
                include=["documents", "metadatas", "distances"],
            )
            if not results or not results.get("documents") or not results["documents"][0]:
                return []
            items = []
            for i in range(len(results["documents"][0])):
                items.append({
                    "content": results["documents"][0][i],
                    "role": results["metadatas"][0][i].get("role", ""),
                    "msg_id": results["metadatas"][0][i].get("msg_id", 0),
                    "distance": results["distances"][0][i] if results.get("distances") else 0,
                })
            return items
        except Exception as e:
            print(f"[VectorStore] 检索失败: {e}")
            return []

    def delete_collection(self, conv_id):
        """删除 conversation 对应的 collection"""
        col_name = f"conv_{conv_id}"
        try:
            self._client.delete_collection(name=col_name)
        except Exception:
            pass
        if conv_id in self._collections:
            del self._collections[conv_id]

    def count(self, conv_id):
        """返回 collection 中的向量数量"""
        try:
            col = self._get_collection(conv_id)
            return col.count()
        except Exception:
            return 0