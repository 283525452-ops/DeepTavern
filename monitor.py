import sys
import json
import markdown
import asyncio
import websockets
import traceback
import requests
import threading
from typing import Optional, List, Tuple, Deque, Any
from collections import deque

from PyQt6.QtCore import Qt, QThread, pyqtSignal, QSize, QTimer
from PyQt6.QtWidgets import (QApplication, QFrame, QVBoxLayout, QHBoxLayout,
                             QTextBrowser, QLabel, QWidget, QListWidget,
                             QListWidgetItem, QMessageBox)
from PyQt6.QtGui import QIcon, QColor, QTextCursor

from qfluentwidgets import (
    FluentWindow,
    NavigationItemPosition,
    FluentIcon as FIF,
    InfoBar,
    InfoBarPosition,
    StateToolTip,
    Theme,
    setTheme,
    isDarkTheme,
    LineEdit,
    PrimaryPushButton,
    StrongBodyLabel,
    CaptionLabel,
    BodyLabel,
    CardWidget,
    SwitchButton,
    ToggleToolButton,
    Flyout,
    FlyoutAnimationType,
    SubtitleLabel,
    PushButton,
    TransparentToolButton
)


# ==========================================
# 0. 全局配置
# ==========================================
class Config:
    """应用配置常量"""
    DEFAULT_IP = "127.0.0.1"
    DEFAULT_PORT = "8000"
    WS_PING_INTERVAL = 20
    WS_OPEN_TIMEOUT = 5
    API_TIMEOUT = 5
    LOG_CACHE_SIZE = 2000
    LOG_DISPLAY_LIMIT = 1000  # QTextBrowser 显示上限
    LOG_TRIM_COUNT = 100      # 超限时删除的条数
    RECONNECT_DELAY = 3
    RENDER_DEBOUNCE_MS = 100  # Markdown 渲染防抖


# ==========================================
# 1. 后台线程：WebSocket 日志监听
# ==========================================
class WebSocketWorker(QThread):
    log_received = pyqtSignal(str, str)
    director_received = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, ip: str = Config.DEFAULT_IP, port: str = Config.DEFAULT_PORT):
        super().__init__()
        self._lock = threading.Lock()
        self._ip = ip
        self._port = port
        self._running = True
        self.loop: Optional[asyncio.AbstractEventLoop] = None

    @property
    def ip(self) -> str:
        with self._lock:
            return self._ip

    @property
    def port(self) -> str:
        with self._lock:
            return self._port

    @property
    def running(self) -> bool:
        with self._lock:
            return self._running

    @running.setter
    def running(self, value: bool):
        with self._lock:
            self._running = value

    def update_address(self, ip: str, port: str) -> None:
        """更新连接地址并重连"""
        with self._lock:
            self._ip = ip
            self._port = port
        self.stop()
        self.running = True
        self.start()

    def run(self) -> None:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

        try:
            self.loop.run_until_complete(self.connect_loop())
        except asyncio.CancelledError:
            pass  # 正常取消
        except Exception as e:
            print(f"Worker Thread Crash: {e}\n{traceback.format_exc()}")
        finally:
            self._cleanup_loop()

    def _cleanup_loop(self) -> None:
        """清理事件循环"""
        if not self.loop:
            return
        try:
            # 取消所有未完成的任务
            pending = asyncio.all_tasks(self.loop)
            for task in pending:
                task.cancel()
            
            if pending:
                self.loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            
            self.loop.run_until_complete(self.loop.shutdown_asyncgens())
            self.loop.close()
        except Exception as e:
            print(f"Loop cleanup error: {e}")
        finally:
            self.loop = None

    async def connect_loop(self) -> None:
        while self.running:
            # 获取当前配置（线程安全）
            with self._lock:
                ip, port = self._ip, self._port
            
            uri = f"ws://{ip}:{port}/ws/logs"
            
            try:
                self.status_changed.emit(f"正在连接: {uri}")
                async with websockets.connect(
                    uri,
                    ping_interval=Config.WS_PING_INTERVAL,
                    open_timeout=Config.WS_OPEN_TIMEOUT
                ) as websocket:
                    self.status_changed.emit(f"已连接到 {uri}")
                    await self._handle_messages(websocket)
                    
            except asyncio.CancelledError:
                self.status_changed.emit("连接已取消")
                break
            except (OSError, ConnectionRefusedError):
                self.status_changed.emit(f"连接失败 (后端未启动?)，{Config.RECONNECT_DELAY}秒后重试...")
                await self._safe_sleep(Config.RECONNECT_DELAY)
            except websockets.exceptions.InvalidURI as e:
                self.status_changed.emit(f"无效的URI: {e}")
                await self._safe_sleep(Config.RECONNECT_DELAY)
            except Exception as e:
                self.status_changed.emit(f"发生错误: {type(e).__name__}，{Config.RECONNECT_DELAY}秒后重试...")
                print(f"WebSocket Error: {traceback.format_exc()}")
                await self._safe_sleep(Config.RECONNECT_DELAY)

    async def _handle_messages(self, websocket) -> None:
        """处理 WebSocket 消息"""
        while self.running:
            try:
                message = await asyncio.wait_for(
                    websocket.recv(),
                    timeout=30  # 30秒超时，用于检查 running 状态
                )
                data = json.loads(message)
                
                if data.get('type') == 'log':
                    self.log_received.emit(data.get('level', 'INFO'), data.get('msg', ''))
                elif data.get('type') == 'director':
                    self.director_received.emit(data.get('content', ''))
                    
            except asyncio.TimeoutError:
                continue  # 超时后继续检查 running 状态
            except websockets.exceptions.ConnectionClosed:
                self.status_changed.emit("连接断开，准备重连...")
                break
            except json.JSONDecodeError as e:
                print(f"JSON decode error: {e}")
            except asyncio.CancelledError:
                break

    async def _safe_sleep(self, seconds: float) -> None:
        """可中断的睡眠"""
        try:
            await asyncio.sleep(seconds)
        except asyncio.CancelledError:
            pass

    def stop(self) -> None:
        """停止工作线程"""
        self.running = False
        
        if self.loop and self.loop.is_running():
            # 在事件循环中取消所有任务
            self.loop.call_soon_threadsafe(self._cancel_tasks)
        
        self.quit()
        if not self.wait(3000):  # 最多等待3秒
            print("Warning: Worker thread did not stop gracefully, terminating...")
            self.terminate()
            self.wait()

    def _cancel_tasks(self) -> None:
        """取消所有任务并停止循环"""
        if not self.loop:
            return
        for task in asyncio.all_tasks(self.loop):
            task.cancel()
        self.loop.stop()


# ==========================================
# 2. 后台线程：API 请求 (存档管理)
# ==========================================
class ApiWorker(QThread):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, ip: str, port: str, action: str, payload: Optional[dict] = None):
        super().__init__()
        self.base_url = f"http://{ip}:{port}/v1"
        self.action = action
        self.payload = payload
        self._is_cancelled = False

    def run(self) -> None:
        try:
            resp = self._make_request()
            
            if self._is_cancelled:
                return
                
            if resp and resp.status_code == 200:
                self.finished.emit(resp.json())
            elif resp:
                self.error.emit(f"API Error {resp.status_code}: {resp.text[:200]}")
                
        except requests.Timeout:
            if not self._is_cancelled:
                self.error.emit("请求超时，请检查后端是否正常运行")
        except requests.ConnectionError:
            if not self._is_cancelled:
                self.error.emit("连接失败，请检查后端地址是否正确")
        except Exception as e:
            if not self._is_cancelled:
                self.error.emit(f"请求错误: {str(e)}")

    def _make_request(self) -> Optional[requests.Response]:
        """执行 HTTP 请求"""
        timeout = Config.API_TIMEOUT
        
        if self.action == 'list':
            return requests.get(f"{self.base_url}/sessions", timeout=timeout)
        elif self.action == 'load':
            return requests.post(f"{self.base_url}/sessions/load", json=self.payload, timeout=timeout)
        elif self.action == 'delete':
            return requests.post(f"{self.base_url}/sessions/delete", json=self.payload, timeout=timeout)
        return None

    def cancel(self) -> None:
        """取消请求（标记，不会中断正在进行的请求）"""
        self._is_cancelled = True


# ==========================================
# 3. 线程管理器
# ==========================================
class ThreadManager:
    """管理 API 线程的生命周期，防止泄漏"""
    
    def __init__(self):
        self._threads: List[QThread] = []
        self._lock = threading.Lock()

    def add(self, thread: QThread) -> None:
        with self._lock:
            # 清理已完成的线程
            self._threads = [t for t in self._threads if t.isRunning()]
            self._threads.append(thread)

    def remove(self, thread: QThread) -> None:
        with self._lock:
            if thread in self._threads:
                self._threads.remove(thread)
        
        # 安排延迟删除
        QTimer.singleShot(0, lambda: self._safe_delete(thread))

    def _safe_delete(self, thread: QThread) -> None:
        try:
            if not thread.isRunning():
                thread.deleteLater()
        except RuntimeError:
            pass  # 对象已被删除

    def cancel_all(self) -> None:
        """取消所有正在运行的线程"""
        with self._lock:
            for thread in self._threads:
                if hasattr(thread, 'cancel'):
                    thread.cancel()
                if thread.isRunning():
                    thread.quit()
                    thread.wait(1000)
            self._threads.clear()


# ==========================================
# 4. 界面组件：连接指引
# ==========================================
class ConnectionGuideWidget(QWidget):
    def __init__(self, ip: str, port: str, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        intro = BodyLabel("在 SillyTavern / RisuAI 中选择 OpenAI (Chat Completion) 并填写以下参数：", self)
        intro.setWordWrap(True)
        layout.addWidget(intro)

        param_style = """
            QLabel { 
                background-color: rgba(128, 128, 128, 0.1); 
                padding: 8px; 
                border-radius: 5px; 
                font-family: 'Consolas', 'Monaco', monospace; 
            }
        """

        layout.addWidget(CaptionLabel("API 地址 (Base URL):"))
        lbl_url = QLabel(f"http://{ip}:{port}/v1", self)
        lbl_url.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl_url.setStyleSheet(param_style)
        layout.addWidget(lbl_url)

        layout.addWidget(CaptionLabel("API Key:"))
        lbl_key = QLabel("sk-deep-tavern", self)
        lbl_key.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        lbl_key.setStyleSheet(param_style)
        layout.addWidget(lbl_key)


# ==========================================
# 5. 界面：系统日志
# ==========================================
class LogInterface(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("LogInterface")
        self.log_cache: Deque[Tuple[str, str]] = deque(maxlen=Config.LOG_CACHE_SIZE)

        layout = QVBoxLayout(self)

        # 工具栏
        tool_layout = QHBoxLayout()
        title = StrongBodyLabel("实时系统日志", self)

        self.help_btn = ToggleToolButton(FIF.HELP, self)
        self.help_btn.clicked.connect(self.show_help)

        self.clear_btn = ToggleToolButton(FIF.DELETE, self)
        self.clear_btn.clicked.connect(self.clear_logs)

        tool_layout.addWidget(title)
        tool_layout.addStretch(1)
        tool_layout.addWidget(self.help_btn)
        tool_layout.addSpacing(5)
        tool_layout.addWidget(self.clear_btn)

        layout.addLayout(tool_layout)

        # 日志视图
        self.log_view = QTextBrowser()
        self.log_view.setOpenExternalLinks(True)
        self.log_view.setStyleSheet("""
            QTextBrowser { 
                background-color: transparent; 
                border: none; 
                font-family: 'Consolas', 'Monaco', 'Courier New', monospace; 
                font-size: 13px; 
            }
        """)
        layout.addWidget(self.log_view)

    def show_help(self) -> None:
        worker = self.window().worker
        content = ConnectionGuideWidget(worker.ip, worker.port, self)
        Flyout.make(
            content,
            target=self.help_btn,
            parent=self.window(),
            aniType=FlyoutAnimationType.PULL_UP,
            isDeleteOnClose=True
        )

    def clear_logs(self) -> None:
        self.log_cache.clear()
        self.log_view.clear()

    def append_log(self, level: str, msg: str) -> None:
        self.log_cache.append((level, msg))
        self._render_single_log(level, msg)
        self._trim_display()

    def _trim_display(self) -> None:
        """限制 QTextBrowser 的内容量，防止内存无限增长"""
        doc = self.log_view.document()
        if doc.blockCount() > Config.LOG_DISPLAY_LIMIT:
            cursor = self.log_view.textCursor()
            cursor.movePosition(QTextCursor.MoveOperation.Start)
            cursor.movePosition(
                QTextCursor.MoveOperation.Down,
                QTextCursor.MoveMode.KeepAnchor,
                Config.LOG_TRIM_COUNT
            )
            cursor.removeSelectedText()

    def _render_single_log(self, level: str, msg: str) -> None:
        is_dark = isDarkTheme()
        base_color = "#e0e0e0" if is_dark else "#333333"

        colors = {
            "INFO": "#98c379" if is_dark else "#2e7d32",
            "WARNING": "#e5c07b" if is_dark else "#ef6c00",
            "ERROR": "#e06c75" if is_dark else "#c62828",
            "DEBUG": "#61afef" if is_dark else "#1565c0"
        }
        c = colors.get(level, base_color)

        # 转义 HTML 特殊字符
        import html
        safe_msg = html.escape(msg)

        html_content = f"""<div style="margin-bottom: 2px;">
            <span style="color: {c}; font-weight: bold;">[{level}]</span> 
            <span style="color: {base_color};">{safe_msg}</span>
        </div>"""
        
        self.log_view.append(html_content)
        self.log_view.moveCursor(QTextCursor.MoveOperation.End)

    def rerender(self) -> None:
        """主题切换时重新渲染所有日志"""
        self.log_view.clear()
        for level, msg in self.log_cache:
            self._render_single_log(level, msg)


# ==========================================
# 6. 界面：导演思维链
# ==========================================
class DirectorInterface(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("DirectorInterface")
        
        layout = QVBoxLayout(self)
        
        # 工具栏
        tool_layout = QHBoxLayout()
        title = StrongBodyLabel("导演思维链", self)
        
        self.clear_btn = TransparentToolButton(FIF.DELETE, self)
        self.clear_btn.clicked.connect(self.clear_content)
        
        tool_layout.addWidget(title)
        tool_layout.addStretch(1)
        tool_layout.addWidget(self.clear_btn)
        layout.addLayout(tool_layout)
        
        # 内容视图
        self.director_view = QTextBrowser()
        self.director_view.setOpenExternalLinks(True)
        self.director_view.setStyleSheet("""
            QTextBrowser { 
                background-color: transparent; 
                border: none; 
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif; 
                padding: 10px; 
            }
        """)
        layout.addWidget(self.director_view)
        
        self.buffer = ""
        self._pending_render = False

    def clear_content(self) -> None:
        self.buffer = ""
        self.director_view.clear()

    def update_content(self, content: str) -> None:
        self.buffer += content
        self._schedule_render()

    def _schedule_render(self) -> None:
        """防抖：合并短时间内的多次更新"""
        if self._pending_render:
            return
        self._pending_render = True
        QTimer.singleShot(Config.RENDER_DEBOUNCE_MS, self._do_render)

    def _do_render(self) -> None:
        self._pending_render = False
        self._render_markdown()

    def _render_markdown(self) -> None:
        clean_content = self.buffer.replace("[导演]:", "").replace("[Director]:", "")
        
        try:
            html_content = markdown.markdown(
                clean_content,
                extensions=['fenced_code', 'tables', 'nl2br']
            )
        except Exception as e:
            print(f"Markdown render error: {e}")
            html_content = f"<pre>{clean_content}</pre>"

        is_dark = isDarkTheme()
        text_color = "#d4d4d4" if is_dark else "#24292f"
        code_bg = "#2d2d2d" if is_dark else "#f6f8fa"
        link_color = "#40a9ff" if is_dark else "#0969da"

        css = f"""<style>
            body {{ color: {text_color}; line-height: 1.6; font-size: 14px; }} 
            h1, h2, h3 {{ color: {link_color}; margin-top: 1em; }} 
            pre {{ background-color: {code_bg}; padding: 10px; border-radius: 5px; overflow-x: auto; }} 
            code {{ background-color: {code_bg}; padding: 2px 4px; border-radius: 3px; font-family: Consolas, Monaco, monospace; }}
            blockquote {{ border-left: 4px solid {link_color}; padding-left: 10px; color: #888; margin: 10px 0; }}
            hr {{ border: 0; border-top: 1px solid #555; }}
            table {{ border-collapse: collapse; width: 100%; }}
            th, td {{ border: 1px solid #555; padding: 8px; text-align: left; }}
        </style>"""

        self.director_view.setHtml(css + html_content)
        self.director_view.moveCursor(QTextCursor.MoveOperation.End)

    def rerender(self) -> None:
        """主题切换时重新渲染"""
        self._render_markdown()


# ==========================================
# 7. 界面：存档管理
# ==========================================
class SessionInterface(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("SessionInterface")
        self.parent_window = parent
        self.thread_manager = ThreadManager()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)

        # 标题栏
        header_layout = QHBoxLayout()
        header_layout.addWidget(StrongBodyLabel("存档管理", self))
        header_layout.addStretch(1)

        self.refresh_btn = TransparentToolButton(FIF.SYNC, self)
        self.refresh_btn.clicked.connect(self.load_sessions)
        header_layout.addWidget(self.refresh_btn)
        layout.addLayout(header_layout)

        layout.addSpacing(10)

        # 列表容器
        self.list_card = CardWidget(self)
        list_layout = QVBoxLayout(self.list_card)
        list_layout.setContentsMargins(0, 0, 0, 0)

        self.session_list = QListWidget()
        self.session_list.setStyleSheet("""
            QListWidget { 
                background: transparent; 
                border: none; 
                outline: none; 
            } 
            QListWidget::item { 
                padding: 10px; 
                border-bottom: 1px solid #333; 
            } 
            QListWidget::item:selected { 
                background: rgba(255, 255, 255, 0.1); 
            }
            QListWidget::item:hover { 
                background: rgba(255, 255, 255, 0.05); 
            }
        """)
        list_layout.addWidget(self.session_list)

        layout.addWidget(self.list_card)

        # 操作按钮
        btn_layout = QHBoxLayout()
        self.load_btn = PrimaryPushButton("加载选中存档", self)
        self.load_btn.clicked.connect(self.do_load)

        self.del_btn = PushButton("删除存档", self)
        self.del_btn.clicked.connect(self.do_delete)

        btn_layout.addStretch(1)
        btn_layout.addWidget(self.del_btn)
        btn_layout.addWidget(self.load_btn)
        layout.addLayout(btn_layout)

    def load_sessions(self) -> None:
        self.session_list.clear()
        self._set_loading(True)

        worker = self.parent_window.worker
        api_thread = ApiWorker(worker.ip, worker.port, 'list')
        
        api_thread.finished.connect(self._on_list_success)
        api_thread.error.connect(self._on_error)
        api_thread.finished.connect(lambda: self._cleanup_thread(api_thread))
        api_thread.error.connect(lambda: self._cleanup_thread(api_thread))
        
        self.thread_manager.add(api_thread)
        api_thread.start()

    def _on_list_success(self, data: dict) -> None:
        self._set_loading(False)
        sessions = data.get('data', [])

        if not sessions:
            item = QListWidgetItem("暂无存档")
            item.setFlags(Qt.ItemFlag.NoItemFlags)
            self.session_list.addItem(item)
            return

        for s in sessions:
            char_name = s.get('character_name', 'Unknown')
            uuid = s.get('uuid', 'N/A')
            created_at = s.get('created_at', '')
            
            text = f"[{char_name}]  {uuid}  \n📅 {created_at}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, uuid)
            self.session_list.addItem(item)

    def do_load(self) -> None:
        item = self.session_list.currentItem()
        if not item:
            InfoBar.warning("提示", "请先选择一个存档", parent=self.parent_window)
            return

        uuid = item.data(Qt.ItemDataRole.UserRole)
        if not uuid:
            return

        self.load_btn.setEnabled(False)
        self.load_btn.setText("加载中...")

        worker = self.parent_window.worker
        api_thread = ApiWorker(worker.ip, worker.port, 'load', {'uuid': uuid})
        
        api_thread.finished.connect(self._on_load_success)
        api_thread.error.connect(self._on_error)
        api_thread.finished.connect(lambda: self._cleanup_thread(api_thread))
        api_thread.error.connect(lambda: self._cleanup_thread(api_thread))
        
        self.thread_manager.add(api_thread)
        api_thread.start()

    def _on_load_success(self, data: dict) -> None:
        self._reset_load_btn()
        char_name = data.get('char', '未知')
        InfoBar.success("成功", f"已加载存档: {char_name}", parent=self.parent_window)

    def do_delete(self) -> None:
        item = self.session_list.currentItem()
        if not item:
            InfoBar.warning("提示", "请先选择一个存档", parent=self.parent_window)
            return

        uuid = item.data(Qt.ItemDataRole.UserRole)
        if not uuid:
            return

        reply = QMessageBox.question(
            self,
            '确认删除',
            f'确定要彻底删除存档 {uuid} 吗？\n此操作不可恢复！',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            self._do_delete_confirmed(uuid)

    def _do_delete_confirmed(self, uuid: str) -> None:
        worker = self.parent_window.worker
        api_thread = ApiWorker(worker.ip, worker.port, 'delete', {'uuid': uuid})
        
        api_thread.finished.connect(self._on_delete_success)
        api_thread.error.connect(self._on_error)
        api_thread.finished.connect(lambda: self._cleanup_thread(api_thread))
        api_thread.error.connect(lambda: self._cleanup_thread(api_thread))
        
        self.thread_manager.add(api_thread)
        api_thread.start()

    def _on_delete_success(self, data: dict) -> None:
        InfoBar.success("删除成功", "存档已移除", parent=self.parent_window)
        self.load_sessions()

    def _on_error(self, msg: str) -> None:
        self._set_loading(False)
        self._reset_load_btn()
        InfoBar.error("错误", msg, parent=self.parent_window)

    def _set_loading(self, loading: bool) -> None:
        self.refresh_btn.setEnabled(not loading)

    def _reset_load_btn(self) -> None:
        self.load_btn.setEnabled(True)
        self.load_btn.setText("加载选中存档")

    def _cleanup_thread(self, thread: ApiWorker) -> None:
        self.thread_manager.remove(thread)

    def cleanup(self) -> None:
        """清理所有线程"""
        self.thread_manager.cancel_all()


# ==========================================
# 8. 界面：设置页面
# ==========================================
class SettingInterface(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent=parent)
        self.setObjectName("SettingInterface")
        self.parent_window = parent
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 30, 30, 30)

        # 连接设置
        layout.addWidget(StrongBodyLabel("连接设置", self))
        layout.addSpacing(10)
        
        self.conn_card = CardWidget(self)
        card_layout = QVBoxLayout(self.conn_card)

        self.ip_input = LineEdit(self)
        self.ip_input.setText(Config.DEFAULT_IP)
        self.ip_input.setPlaceholderText("例如: 127.0.0.1")
        
        self.port_input = LineEdit(self)
        self.port_input.setText(Config.DEFAULT_PORT)
        self.port_input.setPlaceholderText("例如: 8000")
        
        self.save_btn = PrimaryPushButton("保存并重连", self)
        self.save_btn.clicked.connect(self.apply_settings)

        card_layout.addWidget(CaptionLabel("后端 IP 地址"))
        card_layout.addWidget(self.ip_input)
        card_layout.addSpacing(10)
        card_layout.addWidget(CaptionLabel("后端端口 (Port)"))
        card_layout.addWidget(self.port_input)
        card_layout.addSpacing(15)
        card_layout.addWidget(self.save_btn)
        layout.addWidget(self.conn_card)

        # 个性化设置
        layout.addSpacing(30)
        layout.addWidget(StrongBodyLabel("个性化", self))
        layout.addSpacing(10)
        
        self.theme_card = CardWidget(self)
        theme_layout = QHBoxLayout(self.theme_card)
        theme_layout.addWidget(StrongBodyLabel("深色模式", self))
        theme_layout.addStretch(1)
        
        self.theme_switch = SwitchButton(parent=self.theme_card)
        self.theme_switch.setOnText("开")
        self.theme_switch.setOffText("关")
        self.theme_switch.setChecked(True)
        self.theme_switch.checkedChanged.connect(self.toggle_theme)
        theme_layout.addWidget(self.theme_switch)
        layout.addWidget(self.theme_card)

        # 关于信息
        layout.addSpacing(30)
        layout.addWidget(StrongBodyLabel("关于", self))
        layout.addSpacing(10)
        
        about_card = CardWidget(self)
        about_layout = QVBoxLayout(about_card)
        about_layout.addWidget(CaptionLabel("DeepTavern 控制台 v1.0.0"))
        about_layout.addWidget(CaptionLabel("基于 PyQt6 + qfluentwidgets 构建"))
        layout.addWidget(about_card)

        layout.addStretch(1)

    def apply_settings(self) -> None:
        ip = self.ip_input.text().strip()
        port = self.port_input.text().strip()

        # 简单验证
        if not ip:
            InfoBar.warning("警告", "IP 地址不能为空", parent=self.parent_window)
            return
        if not port or not port.isdigit():
            InfoBar.warning("警告", "端口必须是数字", parent=self.parent_window)
            return

        self.parent_window.update_worker_config(ip, port)
        InfoBar.success(
            title='设置已应用',
            content=f'正在连接到 {ip}:{port}...',
            parent=self.parent_window,
            position=InfoBarPosition.TOP_RIGHT
        )

    def toggle_theme(self, is_dark: bool) -> None:
        theme = Theme.DARK if is_dark else Theme.LIGHT
        setTheme(theme)

        # 刷新界面
        self.parent_window.log_interface.rerender()
        self.parent_window.director_interface.rerender()

        InfoBar.info("主题切换", "界面已刷新", parent=self.parent_window)


# ==========================================
# 9. 主窗口
# ==========================================
class DeepTavernWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DeepTavern 控制台 - [未连接]")
        self.resize(1100, 750)
        self.setWindowIcon(FIF.COMMAND_PROMPT.icon())

        setTheme(Theme.DARK)

        # 初始化 Worker（必须在界面之前）
        self.worker = WebSocketWorker(Config.DEFAULT_IP, Config.DEFAULT_PORT)

        # 初始化界面
        self.log_interface = LogInterface(self)
        self.director_interface = DirectorInterface(self)
        self.session_interface = SessionInterface(self)
        self.setting_interface = SettingInterface(self)

        self.init_navigation()
        self.stateTooltip: Optional[StateToolTip] = None

        # 连接信号
        self.worker.log_received.connect(self.log_interface.append_log)
        self.worker.director_received.connect(self.director_interface.update_content)
        self.worker.status_changed.connect(self.handle_status_change)

        # 启动 Worker
        self.worker.start()

    def init_navigation(self) -> None:
        self.addSubInterface(self.log_interface, FIF.COMMAND_PROMPT, "系统终端")
        self.addSubInterface(self.director_interface, FIF.MOVIE, "导演思维链")
        self.addSubInterface(self.session_interface, FIF.SAVE, "存档管理")
        self.addSubInterface(
            self.setting_interface, 
            FIF.SETTING, 
            "设置", 
            NavigationItemPosition.BOTTOM
        )

    def update_worker_config(self, ip: str, port: str) -> None:
        self.worker.update_address(ip, port)

    def handle_status_change(self, msg: str) -> None:
        if "已连接" in msg:
            self.setWindowTitle(f"DeepTavern 控制台 - [{self.worker.ip}:{self.worker.port}]")
            self._close_state_tooltip()
            InfoBar.success(
                title='连接成功',
                content=msg,
                parent=self,
                position=InfoBarPosition.TOP_RIGHT
            )
            # 连接成功后自动刷新存档列表
            QTimer.singleShot(100, self.session_interface.load_sessions)
            
        elif "正在连接" in msg:
            self.setWindowTitle("DeepTavern 控制台 - [连接中...]")
            self._show_state_tooltip()
            
        else:
            self.setWindowTitle("DeepTavern 控制台 - [断开]")
            self._update_state_tooltip_error()

    def _show_state_tooltip(self) -> None:
        if not self.stateTooltip:
            self.stateTooltip = StateToolTip("连接中", "正在寻找后端服务...", self)
            self.stateTooltip.move(self.stateTooltip.getSuitablePos())
            self.stateTooltip.show()

    def _close_state_tooltip(self) -> None:
        if self.stateTooltip:
            try:
                self.stateTooltip.close()
            except RuntimeError:
                pass
            self.stateTooltip = None

    def _update_state_tooltip_error(self) -> None:
        if self.stateTooltip:
            try:
                if self.stateTooltip.isVisible():
                    self.stateTooltip.setContent("连接断开，重试中...")
                    self.stateTooltip.setState(True)
            except RuntimeError:
                self.stateTooltip = None

    def closeEvent(self, event) -> None:
        # 清理所有资源
        self.session_interface.cleanup()
        self.worker.stop()
        super().closeEvent(event)


# ==========================================
# 10. 入口
# ==========================================
def main():
    # PyQt6 默认启用高 DPI 支持，无需手动设置
    app = QApplication(sys.argv)
    
    # 设置应用信息
    app.setApplicationName("DeepTavern Console")
    app.setApplicationVersion("1.0.0")
    
    window = DeepTavernWindow()
    window.show()
    
    sys.exit(app.exec())


if __name__ == '__main__':
    main()
