# scripts/ingest_gui.py
import sys
import os
import json

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (QApplication, QVBoxLayout, QHBoxLayout, QFileDialog, QWidget)

# 引入 qfluentwidgets
from qfluentwidgets import (
    FluentWindow, SubtitleLabel, BodyLabel, CardWidget, 
    PrimaryPushButton, LineEdit, TextEdit, InfoBar, 
    StrongBodyLabel, CaptionLabel, ComboBox, FluentIcon as FIF
)

# 引入后端逻辑
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from scripts.ingest_preset import PresetIngester

class IngestWorker(QThread):
    log_signal = pyqtSignal(str)
    finished_signal = pyqtSignal()

    def __init__(self, file_path, llm_config):
        super().__init__()
        self.file_path = file_path
        self.llm_config = llm_config

    def run(self):
        try:
            ingester = PresetIngester(
                self.llm_config, 
                log_callback=self.log_signal.emit
            )
            ingester.ingest(self.file_path)
        except Exception as e:
            self.log_signal.emit(f"❌ 致命错误: {str(e)}")
        finally:
            self.finished_signal.emit()

class IngestWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DeepTavern 预设清洗工具")
        self.resize(800, 650)
        
        # 使用通用图标防止报错
        self.setWindowIcon(FIF.SYNC.icon())

        # 主容器
        self.main_widget = QWidget()
        # 【修复点】设置 objectName
        self.main_widget.setObjectName("ingestInterface")
        
        self.addSubInterface(self.main_widget, FIF.FOLDER, "清洗控制台")
        
        self.layout = QVBoxLayout(self.main_widget)
        self.layout.setContentsMargins(30, 20, 30, 20)
        self.layout.setSpacing(15)

        # 1. 文件选择区
        self.layout.addWidget(SubtitleLabel("1. 选择预设文件 (SillyTavern 格式)", self))
        
        file_card = CardWidget(self.main_widget)
        file_layout = QHBoxLayout(file_card)
        
        self.path_edit = LineEdit()
        self.path_edit.setPlaceholderText("请选择 .json 文件...")
        self.browse_btn = PrimaryPushButton("浏览", self)
        self.browse_btn.clicked.connect(self.browse_file)
        
        file_layout.addWidget(self.path_edit)
        file_layout.addWidget(self.browse_btn)
        self.layout.addWidget(file_card)

        # 2. LLM 配置区
        self.layout.addWidget(SubtitleLabel("2. 配置清洗用 LLM (推荐高智商模型)", self))
        
        config_card = CardWidget(self.main_widget)
        config_layout = QVBoxLayout(config_card)
        
        # Base URL
        config_layout.addWidget(CaptionLabel("Base URL"))
        self.url_edit = LineEdit()
        self.url_edit.setText("https://api.siliconflow.cn/v1") # 默认值
        config_layout.addWidget(self.url_edit)
        
        # API Key
        config_layout.addWidget(CaptionLabel("API Key"))
        self.key_edit = LineEdit()
        self.key_edit.setPlaceholderText("sk-...")
        self.key_edit.setEchoMode(LineEdit.EchoMode.Password)
        config_layout.addWidget(self.key_edit)
        
        # Model Name
        config_layout.addWidget(CaptionLabel("模型名称"))
        self.model_edit = LineEdit()
        self.model_edit.setText("deepseek-ai/DeepSeek-V3") # 默认值
        self.model_edit.setPlaceholderText("例如: gemini-pro, gpt-4o")
        config_layout.addWidget(self.model_edit)
        
        self.layout.addWidget(config_card)

        # 3. 操作区
        action_layout = QHBoxLayout()
        self.start_btn = PrimaryPushButton("开始清洗入库", self)
        self.start_btn.clicked.connect(self.start_ingest)
        self.start_btn.setFixedWidth(200)
        action_layout.addStretch(1)
        action_layout.addWidget(self.start_btn)
        self.layout.addLayout(action_layout)

        # 4. 日志区
        self.layout.addWidget(StrongBodyLabel("运行日志", self))
        self.log_view = TextEdit()
        self.log_view.setReadOnly(True)
        self.layout.addWidget(self.log_view)

    def browse_file(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self, "选择预设文件", "", "JSON Files (*.json)"
        )
        if file_path:
            self.path_edit.setText(file_path)

    def start_ingest(self):
        path = self.path_edit.text().strip()
        if not os.path.exists(path):
            InfoBar.error("错误", "文件路径不存在！", parent=self)
            return

        api_key = self.key_edit.text().strip()
        if not api_key:
            InfoBar.warning("提示", "请输入 API Key", parent=self)
            return

        llm_config = {
            "base_url": self.url_edit.text().strip(),
            "api_key": api_key,
            "model": self.model_edit.text().strip(),
            "temperature": 0.1
        }

        # 锁定界面
        self.start_btn.setEnabled(False)
        self.start_btn.setText("正在清洗中...")
        self.log_view.clear()

        # 启动线程
        self.worker = IngestWorker(path, llm_config)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def append_log(self, text):
        self.log_view.append(text)
        # 自动滚动到底部
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def on_finished(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("开始清洗入库")
        InfoBar.success("完成", "清洗任务已结束，请查看日志。", parent=self)

if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = IngestWindow()
    w.show()
    sys.exit(app.exec())
