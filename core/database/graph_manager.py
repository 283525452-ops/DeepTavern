# core/database/graph_manager.py
"""
DeepTavern 知识图谱管理器 v2.0

改进内容：
1. [修改] 无向图 → 有向图 (DiGraph)，区分关系方向
2. [新增] 节点向量化，支持语义搜索
3. [新增] 边的置信度/权重，多次提及的关系权重更高
4. [新增] 关系合并，相同实体对的关系会累积而非覆盖
5. [新增] 实体别名支持，"爱丽丝" 和 "Alice" 可以指向同一节点
6. [新增] 批量操作和延迟保存，提升性能
7. [优化] 向量缓存持久化，重启后不需要重新计算
"""

import networkx as nx
import numpy as np
import os
import json
import threading
import time
from typing import List, Dict, Tuple, Optional, Set
from core.utils.logger import logger

# 尝试导入向量化依赖
try:
    from core.database.silicon_client import SiliconFlowEmbedding
    EMBEDDING_AVAILABLE = True
except ImportError:
    EMBEDDING_AVAILABLE = False
    logger.warning("[Graph] SiliconFlowEmbedding not available, falling back to keyword matching")


GRAPH_DIR = "data/graphs"
VECTOR_CACHE_DIR = "data/graphs/vectors"


class GraphManager:
    """
    知识图谱管理器（单例模式）
    
    功能：
    - 存储实体关系三元组
    - 支持语义搜索（节点向量化）
    - 关系权重累积
    - 实体别名管理
    """
    
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super(GraphManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return
        self._init_graph()
        self._initialized = True

    def _init_graph(self):
        """初始化图谱"""
        # [修改] 使用有向图
        self.graph = nx.DiGraph()
        self.current_file_path = None
        self.current_session_uuid = None
        
        # 节点向量缓存 {node_name: embedding_vector}
        self.node_vectors: Dict[str, np.ndarray] = {}
        self.vector_cache_path = None
        
        # 实体别名映射 {alias: canonical_name}
        self.aliases: Dict[str, str] = {}
        
        # 延迟保存控制
        self._dirty = False
        self._save_lock = threading.Lock()
        self._last_save_time = 0
        self._save_interval = 30  # 最少 30 秒保存一次
        
        # 向量化工具
        self.embedding_fn = None
        if EMBEDDING_AVAILABLE:
            try:
                self.embedding_fn = SiliconFlowEmbedding()
                logger.info("[Graph] Embedding function initialized")
            except Exception as e:
                logger.warning(f"[Graph] Failed to init embedding: {e}")
        
        # 确保目录存在
        for dir_path in [GRAPH_DIR, VECTOR_CACHE_DIR]:
            if not os.path.exists(dir_path):
                os.makedirs(dir_path)

    # ==========================================
    # 会话管理
    # ==========================================

    def switch_session(self, session_uuid: str):
        """切换到指定会话的图谱"""
        # 保存当前会话
        if self.current_file_path and self._dirty:
            self._save_now()
        
        self.current_session_uuid = session_uuid
        self.current_file_path = os.path.join(GRAPH_DIR, f"graph_{session_uuid}.gml")
        self.vector_cache_path = os.path.join(VECTOR_CACHE_DIR, f"vectors_{session_uuid}.json")
        
        # 重置
        self.graph = nx.DiGraph()
        self.node_vectors = {}
        self.aliases = {}
        self._dirty = False
        
        # 加载
        self._load_graph()
        self._load_vectors()
        self._load_aliases()
        
        logger.info(f"[Graph] Switched to session: {session_uuid} | "
                   f"Nodes: {self.graph.number_of_nodes()}, Edges: {self.graph.number_of_edges()}")

    def _load_graph(self):
        """加载图谱文件"""
        if not self.current_file_path or not os.path.exists(self.current_file_path):
            logger.info("[Graph] New graph initialized")
            return
        
        try:
            # NetworkX 的 GML 读取默认是无向图，需要指定
            self.graph = nx.read_gml(self.current_file_path)
            
            # 如果加载的是旧的无向图，转换为有向图
            if not self.graph.is_directed():
                logger.info("[Graph] Converting undirected graph to directed")
                self.graph = self.graph.to_directed()
            
            logger.info(f"[Graph] Loaded: {os.path.basename(self.current_file_path)}")
        except Exception as e:
            logger.error(f"[Graph] Load failed: {e}")
            self.graph = nx.DiGraph()

    def _load_vectors(self):
        """加载向量缓存"""
        if not self.vector_cache_path or not os.path.exists(self.vector_cache_path):
            return
        
        try:
            with open(self.vector_cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.node_vectors = {
                k: np.array(v) for k, v in data.get('vectors', {}).items()
            }
            logger.info(f"[Graph] Loaded {len(self.node_vectors)} node vectors from cache")
        except Exception as e:
            logger.warning(f"[Graph] Vector cache load failed: {e}")

    def _load_aliases(self):
        """加载实体别名"""
        alias_path = self.current_file_path.replace('.gml', '_aliases.json') if self.current_file_path else None
        if not alias_path or not os.path.exists(alias_path):
            return
        
        try:
            with open(alias_path, 'r', encoding='utf-8') as f:
                self.aliases = json.load(f)
            logger.info(f"[Graph] Loaded {len(self.aliases)} entity aliases")
        except Exception as e:
            logger.warning(f"[Graph] Alias load failed: {e}")

    # ==========================================
    # 保存逻辑
    # ==========================================

    def save(self):
        """标记需要保存（延迟保存）"""
        self._dirty = True
        
        # 检查是否需要立即保存
        current_time = time.time()
        if current_time - self._last_save_time > self._save_interval:
            self._save_now()

    def _save_now(self):
        """立即保存所有数据"""
        with self._save_lock:
            if not self.current_file_path:
                return
            
            try:
                # 保存图谱
                nx.write_gml(self.graph, self.current_file_path)
                
                # 保存向量缓存
                if self.vector_cache_path and self.node_vectors:
                    vector_data = {
                        'vectors': {k: v.tolist() for k, v in self.node_vectors.items()}
                    }
                    with open(self.vector_cache_path, 'w', encoding='utf-8') as f:
                        json.dump(vector_data, f)
                
                # 保存别名
                if self.aliases:
                    alias_path = self.current_file_path.replace('.gml', '_aliases.json')
                    with open(alias_path, 'w', encoding='utf-8') as f:
                        json.dump(self.aliases, f, ensure_ascii=False)
                
                self._dirty = False
                self._last_save_time = time.time()
                
            except Exception as e:
                logger.error(f"[Graph] Save failed: {e}")

    def flush(self):
        """强制保存（关闭时调用）"""
        if self._dirty:
            self._save_now()

    # ==========================================
    # 向量化功能
    # ==========================================

    def _get_embedding(self, text: str) -> Optional[np.ndarray]:
        """获取文本的向量表示"""
        if not self.embedding_fn:
            return None
        
        try:
            # SiliconFlowEmbedding 的 __call__ 接受列表
            embeddings = self.embedding_fn([text])
            if embeddings and len(embeddings) > 0:
                return np.array(embeddings[0])
        except Exception as e:
            logger.debug(f"[Graph] Embedding failed for '{text[:20]}...': {e}")
        
        return None

    def _ensure_node_vector(self, node_name: str):
        """确保节点有向量表示"""
        if node_name in self.node_vectors:
            return
        
        vec = self._get_embedding(node_name)
        if vec is not None:
            self.node_vectors[node_name] = vec

    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """计算余弦相似度"""
        if vec1 is None or vec2 is None:
            return 0.0
        
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    # ==========================================
    # 实体别名管理
    # ==========================================

    def add_alias(self, alias: str, canonical_name: str):
        """添加实体别名"""
        if not alias or not canonical_name:
            return
        
        alias_lower = alias.lower().strip()
        canonical_lower = canonical_name.lower().strip()
        
        if alias_lower != canonical_lower:
            self.aliases[alias_lower] = canonical_name
            self._dirty = True

    def resolve_entity(self, name: str) -> str:
        """解析实体名称（处理别名）"""
        if not name:
            return name
        
        name_lower = name.lower().strip()
        return self.aliases.get(name_lower, name)

    # ==========================================
    # 三元组操作
    # ==========================================

    def add_triplet(self, source: str, relation: str, target: str, 
                    description: str = "", confidence: float = 1.0):
        """
        添加三元组: (Source) --[Relation]--> (Target)
        
        [改进] 
        - 相同边多次添加会累积权重
        - 自动向量化新节点
        - 支持别名解析
        """
        if not source or not target or not relation:
            return
        
        # 解析别名
        source = self.resolve_entity(source.strip())
        target = self.resolve_entity(target.strip())
        relation = relation.strip()
        
        # 添加/更新节点
        if not self.graph.has_node(source):
            self.graph.add_node(source, type="entity", first_seen=time.time())
            self._ensure_node_vector(source)
        
        if not self.graph.has_node(target):
            self.graph.add_node(target, type="entity", first_seen=time.time())
            self._ensure_node_vector(target)
        
        # 添加/更新边
        if self.graph.has_edge(source, target):
            # 边已存在，累积权重和补充描述
            edge_data = self.graph[source][target]
            old_weight = edge_data.get('weight', 1.0)
            old_relations = edge_data.get('relations', [edge_data.get('relation', '')])
            old_descs = edge_data.get('descriptions', [edge_data.get('desc', '')])
            
            # 累积权重
            new_weight = old_weight + confidence
            
            # 合并关系（去重）
            if relation not in old_relations:
                old_relations.append(relation)
            
            # 合并描述（去重）
            if description and description not in old_descs:
                old_descs.append(description)
            
            self.graph[source][target].update({
                'weight': new_weight,
                'relation': old_relations[0],  # 主关系
                'relations': old_relations,     # 所有关系
                'desc': old_descs[0] if old_descs else '',
                'descriptions': old_descs,
                'last_updated': time.time()
            })
        else:
            # 新边
            self.graph.add_edge(
                source, target,
                relation=relation,
                relations=[relation],
                desc=description,
                descriptions=[description] if description else [],
                weight=confidence,
                first_seen=time.time(),
                last_updated=time.time()
            )
        
        self.save()

    def add_triplets_batch(self, triplets: List[Dict]):
        """
        批量添加三元组
        
        :param triplets: [{"source": "", "relation": "", "target": "", "desc": "", "confidence": 1.0}, ...]
        """
        for t in triplets:
            self.add_triplet(
                source=t.get('source', ''),
                relation=t.get('relation', ''),
                target=t.get('target', ''),
                description=t.get('desc', t.get('description', '')),
                confidence=t.get('confidence', 1.0)
            )
        
        # 批量操作后强制保存
        self._save_now()

    # ==========================================
    # 搜索功能
    # ==========================================

    def search_subgraph(self, query: str, top_k: int = 5, depth: int = 1, 
                        min_weight: float = 0.0) -> str:
        """
        搜索相关子图
        
        [改进]
        - 支持语义搜索（向量匹配）
        - 结果按权重排序
        - 支持最小权重过滤
        
        :param query: 搜索查询（可以是关键词或句子）
        :param top_k: 返回最相关的 top_k 个起始节点
        :param depth: 图遍历深度
        :param min_weight: 最小边权重过滤
        :return: 格式化的关系文本
        """
        if self.graph.number_of_nodes() == 0:
            return ""
        
        # 找到相关节点
        relevant_nodes = self._find_relevant_nodes(query, top_k)
        
        if not relevant_nodes:
            return ""
        
        # 从相关节点出发，收集子图
        result_edges = []
        visited_edges: Set[Tuple[str, str, str]] = set()
        
        for start_node, node_score in relevant_nodes:
            try:
                # 获取以该节点为中心的子图
                subgraph = nx.ego_graph(self.graph, start_node, radius=depth)
                
                for u, v, data in subgraph.edges(data=True):
                    edge_weight = data.get('weight', 1.0)
                    
                    # 权重过滤
                    if edge_weight < min_weight:
                        continue
                    
                    relation = data.get('relation', 'related_to')
                    edge_key = (u, relation, v)
                    
                    if edge_key in visited_edges:
                        continue
                    visited_edges.add(edge_key)
                    
                    # 计算边的综合得分
                    edge_score = node_score * edge_weight
                    
                    desc = data.get('desc', '')
                    all_relations = data.get('relations', [relation])
                    
                    result_edges.append({
                        'source': u,
                        'target': v,
                        'relation': relation,
                        'all_relations': all_relations,
                        'desc': desc,
                        'weight': edge_weight,
                        'score': edge_score
                    })
                    
            except nx.NetworkXError:
                # 节点不存在等错误
                continue
        
        # 按得分排序
        result_edges.sort(key=lambda x: x['score'], reverse=True)
        
        # 格式化输出
        return self._format_edges(result_edges)

    def _find_relevant_nodes(self, query: str, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        找到与查询最相关的节点
        
        优先使用向量匹配，回退到关键词匹配
        """
        nodes = list(self.graph.nodes())
        if not nodes:
            return []
        
        scored_nodes = []
        
        # 尝试向量匹配
        query_vec = self._get_embedding(query) if self.embedding_fn else None
        
        if query_vec is not None and len(self.node_vectors) > 0:
            # 语义搜索模式
            for node in nodes:
                node_vec = self.node_vectors.get(node)
                
                if node_vec is not None:
                    sim = self._cosine_similarity(query_vec, node_vec)
                    scored_nodes.append((node, sim))
                else:
                    # 没有向量的节点，用关键词匹配兜底
                    keyword_score = self._keyword_match_score(query, node)
                    scored_nodes.append((node, keyword_score * 0.5))  # 降权
        else:
            # 关键词匹配模式（回退）
            for node in nodes:
                score = self._keyword_match_score(query, node)
                if score > 0:
                    scored_nodes.append((node, score))
        
        # 排序并返回 top_k
        scored_nodes.sort(key=lambda x: x[1], reverse=True)
        
        # 过滤低分
        min_score = 0.1 if query_vec is not None else 0.01
        filtered = [(n, s) for n, s in scored_nodes if s > min_score]
        
        return filtered[:top_k]

    def _keyword_match_score(self, query: str, node: str) -> float:
        """关键词匹配得分"""
        query_lower = query.lower()
        node_lower = str(node).lower()
        
        # 完全匹配
        if query_lower == node_lower:
            return 1.0
        
        # 包含匹配
        if query_lower in node_lower:
            return 0.8
        if node_lower in query_lower:
            return 0.6
        
        # 词级匹配
        query_words = set(query_lower.split())
        node_words = set(node_lower.split())
        
        if query_words & node_words:
            overlap = len(query_words & node_words)
            total = len(query_words | node_words)
            return 0.5 * overlap / total
        
        return 0.0

    def _format_edges(self, edges: List[Dict]) -> str:
        """格式化边为文本输出"""
        if not edges:
            return ""
        
        lines = []
        for e in edges:
            source = e['source']
            target = e['target']
            relation = e['relation']
            weight = e['weight']
            desc = e.get('desc', '')
            
            # 权重标注
            if weight >= 3:
                weight_tag = "[强关系]"
            elif weight >= 2:
                weight_tag = "[中关系]"
            else:
                weight_tag = ""
            
            line = f"{weight_tag}({source}) --[{relation}]--> ({target})"
            
            # 添加描述
            if desc:
                line += f" | {desc}"
            
            lines.append(line)
        
        return "\n".join(lines)

    # ==========================================
    # 高级查询
    # ==========================================

    def get_entity_relations(self, entity: str) -> Dict:
        """
        获取实体的所有关系
        
        :return: {"outgoing": [...], "incoming": [...]}
        """
        entity = self.resolve_entity(entity)
        
        if not self.graph.has_node(entity):
            return {"outgoing": [], "incoming": []}
        
        outgoing = []
        incoming = []
        
        # 出边
        for _, target, data in self.graph.out_edges(entity, data=True):
            outgoing.append({
                'target': target,
                'relation': data.get('relation', ''),
                'weight': data.get('weight', 1.0)
            })
        
        # 入边
        for source, _, data in self.graph.in_edges(entity, data=True):
            incoming.append({
                'source': source,
                'relation': data.get('relation', ''),
                'weight': data.get('weight', 1.0)
            })
        
        return {
            "outgoing": sorted(outgoing, key=lambda x: x['weight'], reverse=True),
            "incoming": sorted(incoming, key=lambda x: x['weight'], reverse=True)
        }

    def find_path(self, source: str, target: str, max_depth: int = 3) -> Optional[str]:
        """
        查找两个实体之间的路径
        """
        source = self.resolve_entity(source)
        target = self.resolve_entity(target)
        
        if not self.graph.has_node(source) or not self.graph.has_node(target):
            return None
        
        try:
            path = nx.shortest_path(self.graph, source, target)
            
            if len(path) > max_depth + 1:
                return None
            
            # 格式化路径
            path_parts = []
            for i in range(len(path) - 1):
                u, v = path[i], path[i + 1]
                edge_data = self.graph[u][v]
                relation = edge_data.get('relation', '?')
                path_parts.append(f"({u}) --[{relation}]--> ({v})")
            
            return " => ".join(path_parts)
            
        except nx.NetworkXNoPath:
            return None
        except Exception:
            return None

    def get_common_neighbors(self, entity1: str, entity2: str) -> List[str]:
        """查找两个实体的共同关联实体"""
        entity1 = self.resolve_entity(entity1)
        entity2 = self.resolve_entity(entity2)
        
        if not self.graph.has_node(entity1) or not self.graph.has_node(entity2):
            return []
        
        # 获取邻居（忽略方向）
        neighbors1 = set(self.graph.predecessors(entity1)) | set(self.graph.successors(entity1))
        neighbors2 = set(self.graph.predecessors(entity2)) | set(self.graph.successors(entity2))
        
        return list(neighbors1 & neighbors2)

    # ==========================================
    # 图谱维护
    # ==========================================

    def merge_entities(self, entity1: str, entity2: str, canonical_name: str = None):
        """
        合并两个实体节点
        
        :param entity1: 实体1
        :param entity2: 实体2
        :param canonical_name: 合并后的标准名称（默认用 entity1）
        """
        if not self.graph.has_node(entity1) and not self.graph.has_node(entity2):
            return
        
        canonical = canonical_name or entity1
        other = entity2 if canonical == entity1 else entity1
        
        if not self.graph.has_node(other):
            return
        
        # 确保 canonical 节点存在
        if not self.graph.has_node(canonical):
            self.graph.add_node(canonical, type="entity")
        
        # 转移所有边到 canonical
        for source, _, data in list(self.graph.in_edges(other, data=True)):
            if source != canonical:
                self.add_triplet(source, data.get('relation', ''), canonical, 
                               data.get('desc', ''), data.get('weight', 1.0))
        
        for _, target, data in list(self.graph.out_edges(other, data=True)):
            if target != canonical:
                self.add_triplet(canonical, data.get('relation', ''), target,
                               data.get('desc', ''), data.get('weight', 1.0))
        
        # 删除旧节点
        self.graph.remove_node(other)
        
        # 添加别名
        self.add_alias(other, canonical)
        
        # 转移向量
        if other in self.node_vectors and canonical not in self.node_vectors:
            self.node_vectors[canonical] = self.node_vectors.pop(other)
        elif other in self.node_vectors:
            del self.node_vectors[other]
        
        self.save()
        logger.info(f"[Graph] Merged '{other}' into '{canonical}'")

    def prune_weak_edges(self, min_weight: float = 0.5):
        """
        删除低权重的边
        """
        edges_to_remove = []
        
        for u, v, data in self.graph.edges(data=True):
            if data.get('weight', 1.0) < min_weight:
                edges_to_remove.append((u, v))
        
        for u, v in edges_to_remove:
            self.graph.remove_edge(u, v)
        
        if edges_to_remove:
            self._save_now()
            logger.info(f"[Graph] Pruned {len(edges_to_remove)} weak edges")

    def prune_orphan_nodes(self):
        """删除孤立节点（没有任何边）"""
        orphans = [n for n in self.graph.nodes() if self.graph.degree(n) == 0]
        
        for node in orphans:
            self.graph.remove_node(node)
            if node in self.node_vectors:
                del self.node_vectors[node]
        
        if orphans:
            self._save_now()
            logger.info(f"[Graph] Removed {len(orphans)} orphan nodes")

    # ==========================================
    # 统计与调试
    # ==========================================

    def get_stats(self) -> str:
        """获取图谱统计信息"""
        node_count = self.graph.number_of_nodes()
        edge_count = self.graph.number_of_edges()
        vector_count = len(self.node_vectors)
        alias_count = len(self.aliases)
        
        return (f"Nodes: {node_count}, Edges: {edge_count}, "
                f"Vectors: {vector_count}, Aliases: {alias_count}")

    def get_detailed_stats(self) -> Dict:
        """获取详细统计信息"""
        if self.graph.number_of_nodes() == 0:
            return {"empty": True}
        
        weights = [d.get('weight', 1.0) for _, _, d in self.graph.edges(data=True)]
        
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
            "vectors_cached": len(self.node_vectors),
            "aliases": len(self.aliases),
            "avg_edge_weight": sum(weights) / len(weights) if weights else 0,
            "max_edge_weight": max(weights) if weights else 0,
            "density": nx.density(self.graph),
            "is_connected": nx.is_weakly_connected(self.graph) if self.graph.number_of_nodes() > 0 else False
        }

    def export_for_visualization(self) -> Dict:
        """导出为前端可视化格式（如 vis.js, D3.js）"""
        nodes = []
        for node in self.graph.nodes():
            nodes.append({
                "id": node,
                "label": node,
                "type": self.graph.nodes[node].get('type', 'entity')
            })
        
        edges = []
        for u, v, data in self.graph.edges(data=True):
            edges.append({
                "from": u,
                "to": v,
                "label": data.get('relation', ''),
                "weight": data.get('weight', 1.0)
            })
        
        return {"nodes": nodes, "edges": edges}

    # ==========================================
    # 会话清理
    # ==========================================

    def delete_graph(self, session_uuid: str):
        """删除指定会话的图谱"""
        target_path = os.path.join(GRAPH_DIR, f"graph_{session_uuid}.gml")
        vector_path = os.path.join(VECTOR_CACHE_DIR, f"vectors_{session_uuid}.json")
        alias_path = os.path.join(GRAPH_DIR, f"graph_{session_uuid}_aliases.json")
        
        files_to_delete = [target_path, vector_path, alias_path]
        
        for file_path in files_to_delete:
            if os.path.exists(file_path):
                try:
                    os.remove(file_path)
                    logger.info(f"[Graph] Deleted: {file_path}")
                except Exception as e:
                    logger.error(f"[Graph] Delete failed for {file_path}: {e}")
        
        # 如果删除的是当前会话，重置
        if self.current_session_uuid == session_uuid:
            self.graph = nx.DiGraph()
            self.node_vectors = {}
            self.aliases = {}
            self.current_file_path = None
            self.current_session_uuid = None

    def clear_current_graph(self):
        """清空当前图谱（保留文件）"""
        self.graph = nx.DiGraph()
        self.node_vectors = {}
        self._dirty = True
        self.save()
        logger.info("[Graph] Current graph cleared")
