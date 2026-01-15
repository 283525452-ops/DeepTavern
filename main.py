# main.py
"""
DeepTavern API Server v4.5.0
优化版本 - 修复流式传输、线程安全、WebSocket 等问题
"""

import uvicorn
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
import json
import sys
import os
import logging
import time
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from core.workflow.manager import WorkflowManager
from core.utils.logger import logger


# ============================================================================
# 全局配置
# ============================================================================

# 线程池，用于运行同步阻塞代码
executor = ThreadPoolExecutor(max_workers=8)

# 全局事件循环引用（用于跨线程通信）
main_event_loop: Optional[asyncio.AbstractEventLoop] = None


# ============================================================================
# WebSocket 连接管理器
# ============================================================================

class ConnectionManager:
    """
    WebSocket 连接管理器
    - 管理所有活跃的 WebSocket 连接
    - 维护日志缓存
    - 支持广播消息
    """
    
    def __init__(self, max_buffer_size: int = 200):
        self.active_connections: List[WebSocket] = []
        self.log_buffer: List[str] = []
        self.max_buffer_size = max_buffer_size
        
        # 异步锁（用于异步上下文）
        self._async_lock: Optional[asyncio.Lock] = None
        # 同步锁（用于同步上下文，如日志处理器）
        self._sync_lock = threading.Lock()
    
    @property
    def async_lock(self) -> asyncio.Lock:
        """懒加载异步锁，确保在事件循环中创建"""
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock
    
    async def connect(self, websocket: WebSocket) -> bool:
        """
        接受 WebSocket 连接
        返回是否成功连接
        """
        try:
            await websocket.accept()
            
            async with self.async_lock:
                self.active_connections.append(websocket)
                connection_count = len(self.active_connections)
                
                # 发送缓存的日志
                buffer_copy = self.log_buffer.copy()
            
            # 在锁外发送缓存，避免长时间持有锁
            for log_msg in buffer_copy:
                try:
                    await websocket.send_text(log_msg)
                except Exception:
                    # 发送失败，连接可能已断开
                    await self.disconnect(websocket)
                    return False
            
            logger.info(f"[WS] 客户端已连接，当前连接数: {connection_count}")
            return True
            
        except Exception as e:
            logger.warning(f"[WS] 连接失败: {e}")
            return False

    async def disconnect(self, websocket: WebSocket):
        """断开 WebSocket 连接"""
        async with self.async_lock:
            if websocket in self.active_connections:
                self.active_connections.remove(websocket)
                logger.info(f"[WS] 客户端已断开，当前连接数: {len(self.active_connections)}")

    async def broadcast(self, message: str):
        """广播消息给所有连接的客户端"""
        async with self.async_lock:
            # 添加到缓存
            self.log_buffer.append(message)
            while len(self.log_buffer) > self.max_buffer_size:
                self.log_buffer.pop(0)
            
            # 记录需要移除的死连接
            dead_connections: List[WebSocket] = []
            
            # 广播给所有客户端
            for connection in self.active_connections:
                try:
                    await connection.send_text(message)
                except Exception:
                    dead_connections.append(connection)
            
            # 移除死连接
            for conn in dead_connections:
                if conn in self.active_connections:
                    self.active_connections.remove(conn)

    def sync_add_to_buffer(self, message: str):
        """
        线程安全地添加消息到缓存
        用于同步上下文（如日志处理器在其他线程中调用）
        """
        with self._sync_lock:
            self.log_buffer.append(message)
            while len(self.log_buffer) > self.max_buffer_size:
                self.log_buffer.pop(0)

    def broadcast_threadsafe(self, message: str):
        """
        线程安全的广播方法
        可从任何线程调用
        """
        global main_event_loop
        
        if main_event_loop and main_event_loop.is_running():
            # 使用线程安全的方式调度协程
            asyncio.run_coroutine_threadsafe(
                self.broadcast(message),
                main_event_loop
            )
        else:
            # 事件循环不可用，只添加到缓存
            self.sync_add_to_buffer(message)

    @property
    def connection_count(self) -> int:
        """当前连接数"""
        return len(self.active_connections)


# 全局连接管理器实例
manager = ConnectionManager()


# ============================================================================
# WebSocket 日志处理器
# ============================================================================

class WebSocketLogHandler(logging.Handler):
    """
    自定义日志处理器
    将日志消息通过 WebSocket 广播给所有连接的客户端
    """
    
    def __init__(self, connection_manager: ConnectionManager):
        super().__init__()
        self.connection_manager = connection_manager
    
    def emit(self, record: logging.LogRecord):
        try:
            # 格式化日志消息
            log_entry = self.format(record)
            
            # 构建 JSON 负载
            payload = json.dumps({
                "type": "log",
                "level": record.levelname,
                "msg": log_entry,
                "timestamp": time.time()
            }, ensure_ascii=False)
            
            # 使用线程安全的广播方法
            self.connection_manager.broadcast_threadsafe(payload)
            
        except Exception:
            # 日志处理器中的异常不应该影响主程序
            self.handleError(record)


# 配置日志处理器
def setup_websocket_logger():
    """设置 WebSocket 日志处理器"""
    # 移除已存在的 WebSocket 处理器
    handlers_to_remove = [
        h for h in logger.handlers 
        if isinstance(h, WebSocketLogHandler)
    ]
    for h in handlers_to_remove:
        logger.removeHandler(h)
    
    # 添加新的处理器
    ws_handler = WebSocketLogHandler(manager)
    ws_handler.setFormatter(
        logging.Formatter('%(asctime)s - [%(levelname)s] - %(message)s')
    )
    ws_handler.setLevel(logging.INFO)
    logger.addHandler(ws_handler)

setup_websocket_logger()


# ============================================================================
# 工作流管理器
# ============================================================================

# 全局工作流实例
# 注意：在生产环境中，建议使用会话级别的工作流管理
workflow = WorkflowManager()


# ============================================================================
# FastAPI 应用
# ============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    global main_event_loop
    
    # 启动时
    main_event_loop = asyncio.get_running_loop()
    logger.info("🚀 DeepTavern API Server 已启动")
    logger.info(f"📡 API 文档: http://localhost:8000/docs")
    logger.info(f"🔌 WebSocket: ws://localhost:8000/ws/logs")
    
    yield
    
    # 关闭时
    logger.info("👋 DeepTavern API Server 正在关闭...")
    executor.shutdown(wait=False)
    main_event_loop = None


app = FastAPI(
    title="DeepTavern API",
    version="4.5.0",
    description="DeepTavern 核心 API 服务",
    lifespan=lifespan
)

# CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================================
# 数据模型
# ============================================================================

class CreateSessionRequest(BaseModel):
    user_name: str = "Player"
    char_name: str = "AI Assistant"
    char_persona: Optional[str] = None

class LoadSessionRequest(BaseModel):
    uuid: str

class DeleteSessionRequest(BaseModel):
    uuid: str

class RollbackRequest(BaseModel):
    message_id: int

class ChatRequest(BaseModel):
    messages: Optional[List[Dict[str, Any]]] = None
    model: Optional[str] = "default"
    stream: bool = True
    temperature: Optional[float] = 0.7
    top_p: Optional[float] = 1.0
    input: Optional[str] = None
    lite_mode: bool = False
    deep_mode: bool = False


# ============================================================================
# 工具函数
# ============================================================================

def extract_user_input(req: ChatRequest) -> str:
    """从请求中提取用户输入"""
    if req.input:
        return req.input
    
    if req.messages:
        for msg in reversed(req.messages):
            if msg.get('role') == 'user':
                content = msg.get('content', '')
                
                # 处理多模态内容（如图文混合）
                if isinstance(content, list):
                    text_parts = [
                        item.get('text', '') 
                        for item in content 
                        if item.get('type') == 'text'
                    ]
                    return " ".join(text_parts)
                
                return str(content)
    
    return ""


async def run_sync_generator_async(sync_gen_func, *args, **kwargs):
    """
    将同步生成器转换为异步生成器
    使用队列在线程池和事件循环之间传递数据
    """
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()
    
    def producer():
        """在线程池中运行的生产者"""
        try:
            for item in sync_gen_func(*args, **kwargs):
                asyncio.run_coroutine_threadsafe(
                    queue.put(("data", item)), 
                    loop
                )
            asyncio.run_coroutine_threadsafe(
                queue.put(("done", None)), 
                loop
            )
        except Exception as e:
            asyncio.run_coroutine_threadsafe(
                queue.put(("error", e)), 
                loop
            )
    
    # 在线程池中启动生产者
    loop.run_in_executor(executor, producer)
    
    # 异步消费队列
    while True:
        try:
            msg_type, data = await asyncio.wait_for(
                queue.get(), 
                timeout=300  # 5分钟超时
            )
            
            if msg_type == "done":
                break
            elif msg_type == "error":
                raise data
            elif msg_type == "data":
                yield data
                
        except asyncio.TimeoutError:
            raise TimeoutError("生成器执行超时")


# ============================================================================
# WebSocket 路由
# ============================================================================

@app.websocket("/ws/logs")
async def websocket_endpoint(websocket: WebSocket):
    """
    WebSocket 端点，用于实时日志推送
    """
    connected = await manager.connect(websocket)
    if not connected:
        return
    
    try:
        while True:
            # 等待客户端消息（心跳或命令）
            data = await websocket.receive_text()
            
            # 处理心跳
            if data == "ping":
                await websocket.send_text(json.dumps({
                    "type": "pong",
                    "timestamp": time.time()
                }))
            
            # 可扩展：处理其他命令
            elif data.startswith("{"):
                try:
                    cmd = json.loads(data)
                    cmd_type = cmd.get("type")
                    
                    if cmd_type == "get_status":
                        await websocket.send_text(json.dumps({
                            "type": "status",
                            "connections": manager.connection_count,
                            "session": workflow.current_session_uuid
                        }))
                except json.JSONDecodeError:
                    pass
                    
    except WebSocketDisconnect:
        await manager.disconnect(websocket)
    except RuntimeError as e:
        # "WebSocket is not connected" 等运行时错误
        logger.debug(f"[WS] RuntimeError: {e}")
        await manager.disconnect(websocket)
    except Exception as e:
        logger.warning(f"[WS] 未预期的错误: {e}")
        await manager.disconnect(websocket)


# ============================================================================
# REST API 路由
# ============================================================================

@app.get("/")
async def root():
    """根路由，返回服务状态"""
    return {
        "status": "running",
        "name": "DeepTavern Core",
        "version": "4.5.0",
        "docs": "/docs",
        "websocket": "/ws/logs"
    }


@app.get("/health")
async def health_check():
    """健康检查端点"""
    return {
        "status": "healthy",
        "timestamp": time.time(),
        "websocket_connections": manager.connection_count,
        "active_session": workflow.current_session_uuid
    }


# === 会话管理 ===

@app.get("/v1/sessions")
async def list_sessions():
    """列出所有会话"""
    try:
        sessions = workflow.list_all_sessions()
        return {
            "success": True,
            "data": sessions,
            "count": len(sessions) if sessions else 0
        }
    except Exception as e:
        logger.error(f"列出会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/sessions/new")
async def create_session(req: CreateSessionRequest):
    """创建新会话"""
    try:
        uuid = workflow.start_new_session(
            req.user_name, 
            req.char_name, 
            req.char_persona
        )
        logger.info(f"创建新会话: {uuid}")
        return {
            "success": True,
            "uuid": uuid,
            "message": "会话已创建"
        }
    except Exception as e:
        logger.error(f"创建会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/sessions/load")
async def load_session(req: LoadSessionRequest):
    """加载已有会话"""
    try:
        if workflow.load_session(req.uuid):
            logger.info(f"加载会话: {req.uuid}")
            return {
                "success": True,
                "uuid": req.uuid,
                "message": "会话已加载",
                "char_name": getattr(workflow, 'char_name', None)
            }
        raise HTTPException(status_code=404, detail="会话不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"加载会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/sessions/delete")
async def delete_session(req: DeleteSessionRequest):
    """删除会话"""
    try:
        if workflow.delete_session(req.uuid):
            logger.info(f"删除会话: {req.uuid}")
            return {
                "success": True,
                "message": f"会话 {req.uuid} 已删除"
            }
        raise HTTPException(status_code=404, detail="会话不存在")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"删除会话失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === 聊天接口 ===

@app.post("/v1/chat/completions")
async def chat_completions(req: ChatRequest):
    """
    聊天完成接口
    兼容 OpenAI API 格式
    支持流式和非流式响应
    """
    # 确保有活跃会话
    if not workflow.current_session_uuid:
        workflow.start_new_session()
        logger.info("自动创建新会话")
    
    # 提取用户输入
    user_input = extract_user_input(req)
    if not user_input:
        raise HTTPException(status_code=400, detail="未找到用户输入")
    
    logger.info(f"收到聊天请求: {user_input[:50]}...")
    
    if req.stream:
        # 流式响应
        return StreamingResponse(
            stream_chat_response(user_input, req),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",  # 禁用 Nginx 缓冲
            }
        )
    else:
        # 非流式响应
        return await non_stream_chat_response(user_input, req)


async def stream_chat_response(user_input: str, req: ChatRequest):
    """
    流式聊天响应生成器
    使用异步队列实现真正的流式传输
    """
    chunk_id = f"chatcmpl-{int(time.time() * 1000)}"
    created = int(time.time())
    full_response = ""
    
    try:
        # 使用异步包装器处理同步生成器
        async for chunk in run_sync_generator_async(
            workflow.chat,
            user_input,
            req.deep_mode,
            req.lite_mode
        ):
            full_response += chunk
            
            # 广播导演思维链（如果有）
            if "[导演]:" in chunk or "[Director]:" in chunk:
                try:
                    asyncio.create_task(
                        manager.broadcast(json.dumps({
                            "type": "director",
                            "content": chunk,
                            "timestamp": time.time()
                        }, ensure_ascii=False))
                    )
                except Exception:
                    pass
            
            # 构建 SSE 数据包
            data = {
                "id": chunk_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": "deep-tavern",
                "choices": [{
                    "index": 0,
                    "delta": {"content": chunk},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
        
        # 发送完成信号
        finish_data = {
            "id": chunk_id,
            "object": "chat.completion.chunk",
            "created": created,
            "model": "deep-tavern",
            "choices": [{
                "index": 0,
                "delta": {},
                "finish_reason": "stop"
            }]
        }
        yield f"data: {json.dumps(finish_data)}\n\n"
        yield "data: [DONE]\n\n"
        
        logger.info(f"流式响应完成，总长度: {len(full_response)}")
        
    except TimeoutError:
        logger.error("聊天响应超时")
        error_data = {"error": {"message": "响应超时", "type": "timeout"}}
        yield f"data: {json.dumps(error_data)}\n\n"
        
    except Exception as e:
        logger.error(f"聊天错误: {e}")
        error_data = {"error": {"message": str(e), "type": "internal_error"}}
        yield f"data: {json.dumps(error_data)}\n\n"


async def non_stream_chat_response(user_input: str, req: ChatRequest) -> Dict:
    """非流式聊天响应"""
    try:
        full_response = ""
        
        async for chunk in run_sync_generator_async(
            workflow.chat,
            user_input,
            req.deep_mode,
            req.lite_mode
        ):
            full_response += chunk
        
        return {
            "id": f"chatcmpl-{int(time.time() * 1000)}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": "deep-tavern",
            "choices": [{
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": full_response
                },
                "finish_reason": "stop"
            }],
            "usage": {
                "prompt_tokens": len(user_input),
                "completion_tokens": len(full_response),
                "total_tokens": len(user_input) + len(full_response)
            }
        }
        
    except Exception as e:
        logger.error(f"非流式聊天错误: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# === 历史记录 ===

@app.get("/v1/history")
async def get_history(page: int = 1, size: int = 50):
    """获取聊天历史"""
    if not workflow.current_session_uuid:
        raise HTTPException(status_code=400, detail="没有加载的会话")
    
    try:
        history = workflow.get_full_history(page, size)
        return {
            "success": True,
            "data": history,
            "page": page,
            "size": size
        }
    except Exception as e:
        logger.error(f"获取历史失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/v1/rollback")
async def rollback(req: RollbackRequest):
    """回滚到指定消息"""
    if not workflow.current_session_uuid:
        raise HTTPException(status_code=400, detail="没有加载的会话")
    
    try:
        if workflow.rollback(req.message_id):
            logger.info(f"回滚到消息 ID: {req.message_id}")
            return {
                "success": True,
                "message": f"已回滚到消息 {req.message_id}"
            }
        raise HTTPException(status_code=500, detail="回滚失败")
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"回滚失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ============================================================================
# 调试接口（可选，生产环境建议禁用）
# ============================================================================

@app.get("/debug/connections")
async def debug_connections():
    """调试：查看当前 WebSocket 连接"""
    return {
        "active_connections": manager.connection_count,
        "buffer_size": len(manager.log_buffer),
        "max_buffer_size": manager.max_buffer_size
    }


@app.post("/debug/broadcast")
async def debug_broadcast(message: str = "Test broadcast"):
    """调试：发送测试广播"""
    await manager.broadcast(json.dumps({
        "type": "debug",
        "message": message,
        "timestamp": time.time()
    }))
    return {"success": True, "message": "广播已发送"}


# ============================================================================
# 启动入口
# ============================================================================

if __name__ == "__main__":
    print("""
    ╔══════════════════════════════════════════════════════════╗
    ║                                                          ║
    ║   🏰 DeepTavern API Server v4.5.0                        ║
    ║                                                          ║
    ║   API Docs:  http://localhost:8000/docs                  ║
    ║   WebSocket: ws://localhost:8000/ws/logs                 ║
    ║                                                          ║
    ╚══════════════════════════════════════════════════════════╝
    """)
    
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        # WebSocket 心跳配置
        ws_ping_interval=20,
        ws_ping_timeout=20,
        # 日志配置
        access_log=True,
        log_level="info",
        # 性能配置
        loop="auto",
        http="auto",
    )
