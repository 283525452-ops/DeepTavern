# core/database/vector_store.py
import chromadb
import os
from core.utils.logger import logger
from core.database.silicon_client import SiliconFlowEmbedding, rerank_documents

VECTOR_DB_PATH = "data/chroma_db"

class VectorStore:
    def __init__(self, collection_name="long_term_memory"):
        """
        初始化向量数据库客户端
        :param collection_name: 集合名称，默认为 'long_term_memory' (剧情记忆)，
                               也可以是 'rules_memory' (规则库) 或其他。
        """
        # logger.info(f"Initializing Vector DB (Collection: {collection_name})...")
        self.client = chromadb.PersistentClient(path=VECTOR_DB_PATH)
        
        # 使用自定义的硅基流动 Embedding 函数
        self.ef = SiliconFlowEmbedding()
        
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            embedding_function=self.ef
        )
        
        # [新增] 初始化检查：只有当库是空的且是主记忆库时，才写入占位符
        # 规则库不需要占位符，因为我们会通过脚本批量导入
        if collection_name == "long_term_memory" and self.collection.count() == 0:
            logger.info("向量库为空，写入初始占位符...")
            self.collection.add(
                documents=["系统初始化: 记忆库已建立。"],
                metadatas=[{"type": "init", "timestamp": "0", "timeline_index": 0}],
                ids=["init_0"]
            )

    def add_memory(self, text: str, metadata: dict, memory_id: str):
        """
        存入记忆 (自动调用 API 获取向量)
        """
        # logger.debug(f"存储记忆: {text[:30]}...")
        self.collection.add(
            documents=[text],
            metadatas=[metadata],
            ids=[memory_id]
        )

    def search(self, query: str, n_results: int = 5, filter_dict: dict = None) -> list:
        """
        两阶段检索：向量粗排 -> 模型重排
        """
        # logger.info(f"正在检索: '{query}'")
        
        # 1. 向量检索
        results = self.collection.query(
            query_texts=[query],
            n_results=n_results, 
            where=filter_dict
        )
        
        # 检查是否检索到数据
        if not results['documents'] or not results['documents'][0]:
            return []

        docs = results['documents'][0]
        metas = results['metadatas'][0]
        ids = results['ids'][0]
        
        # 2. 调用重排 API (Rerank)
        # 注意：如果检索结果很少，重排可能会报错或没必要，这里加个简单判断
        if len(docs) > 0:
            try:
                reranked_scores = rerank_documents(query, docs)
            except Exception as e:
                logger.warning(f"Rerank failed, falling back to vector scores: {e}")
                # 降级：构造一个伪造的 rerank 结果结构
                reranked_scores = [{'index': i, 'relevance_score': 0.0} for i in range(len(docs))]
        else:
            return []
        
        # 3. 根据重排分数重新组合结果
        final_results = []
        for item in reranked_scores:
            idx = item['index']
            score = item['relevance_score']
            
            meta = metas[idx]
            original_content = docs[idx]
            
            # --- 格式化前缀逻辑 ---
            formatted_content = original_content
            
            # 仅对 'long_term_memory' (剧情记忆) 添加时间轴前缀
            if self.collection.name == "long_term_memory":
                prefix_parts = []
                
                # 1. 时间轴 Index
                if 'timeline_index' in meta:
                    prefix_parts.append(f"[Index:{meta['timeline_index']}]")
                elif 'chunk_index' in meta:
                    prefix_parts.append(f"[片段:{meta['chunk_index']}]")
                elif 'start_id' in meta:
                    prefix_parts.append(f"[ID:{meta['start_id']}]")
                    
                # 2. 情感标签
                if 'emotions' in meta:
                    prefix_parts.append(f"[情感:{meta['emotions']}]")
                    
                # 3. 时间戳
                if 'timestamp' in meta and not prefix_parts:
                    # 尝试格式化时间戳
                    try:
                        ts = str(meta['timestamp']).split('T')[0]
                        prefix_parts.append(f"[日期:{ts}]")
                    except: pass
                
                # 组合前缀
                if prefix_parts:
                    prefix_str = " ".join(prefix_parts)
                    formatted_content = f"{prefix_str} {original_content}"

            # 对于 'rules_memory'，我们不需要加前缀，直接返回规则内容即可
            # 或者可以加一个简单的 [Category] 标记，但这通常在内容里已经有了

            final_results.append({
                "content": formatted_content, # 返回处理后的文本
                "metadata": meta,
                "id": ids[idx],
                "score": score
            })
            
        # logger.info(f"重排完成，返回 {len(final_results)} 条结果")
        return final_results

    def exists(self, doc_id: str) -> bool:
        """检查某个 ID 是否存在 (用于去重)"""
        res = self.collection.get(ids=[doc_id])
        return len(res['ids']) > 0

    # [新增] 删除指定会话的记忆
    def delete_session_memories(self, session_uuid):
        """删除指定会话的所有向量记忆"""
        try:
            # ChromaDB 的 delete 支持 where 过滤
            self.collection.delete(where={"session_id": session_uuid})
            logger.info(f"[Vector] Deleted memories for session: {session_uuid}")
        except Exception as e:
            logger.error(f"[Vector] Delete failed: {e}")
