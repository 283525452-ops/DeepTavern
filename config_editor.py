import sys
import json
import os
from copy import deepcopy

from PyQt6.QtCore import Qt, pyqtSignal, QThread
from PyQt6.QtWidgets import (QApplication, QFrame, QVBoxLayout, QHBoxLayout, 
                             QFileDialog, QWidget, QListWidget, QListWidgetItem,
                             QScrollArea)

from qfluentwidgets import (
    FluentWindow, NavigationItemPosition, FluentIcon as FIF,
    SubtitleLabel, StrongBodyLabel, BodyLabel, CaptionLabel,
    CardWidget, PrimaryPushButton, PushButton, LineEdit, 
    TextEdit, ComboBox, DoubleSpinBox, InfoBar, InfoBarPosition,
    TransparentToolButton
)

# 引入清洗后端逻辑
from scripts.ingest_preset import PresetIngester

CONFIG_FILE = "config.json"
TEMPLATE_FILE = "config.json.template"

# ============================================================================
# 数据管理类
# ============================================================================
class ConfigData:
    """单例数据管理"""
    _data = {}
    
    @classmethod
    def load(cls):
        path = CONFIG_FILE if os.path.exists(CONFIG_FILE) else TEMPLATE_FILE
        if not os.path.exists(path):
            cls._data = {"providers": {}, "vector": {}, "roles": []}
            return
            
        try:
            with open(path, 'r', encoding='utf-8') as f:
                cls._data = json.load(f)
        except Exception as e:
            print(f"Error loading config: {e}")

    @classmethod
    def save(cls):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(cls._data, f, indent=4, ensure_ascii=False)
            return True
        except Exception as e:
            print(f"Error saving config: {e}")
            return False

    @classmethod
    def get_providers(cls):
        return cls._data.get("providers", {})
        
    @classmethod
    def get_provider_keys(cls):
        return list(cls._data.get("providers", {}).keys())

    @classmethod
    def set_providers(cls, providers):
        cls._data["providers"] = providers

    @classmethod
    def get_vector(cls):
        return cls._data.get("vector", {})

    @classmethod
    def set_vector(cls, vector):
        cls._data["vector"] = vector

    @classmethod
    def get_roles(cls):
        return cls._data.get("roles", [])

    @classmethod
    def set_roles(cls, roles):
        cls._data["roles"] = roles

# ============================================================================
# 界面 1: 服务商配置
# ============================================================================
class ProviderInterface(QScrollArea):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("ProviderInterface")
        self.setWidgetResizable(True)
        self.setStyleSheet("QScrollArea {background: transparent; border: none;}")
        
        self.scroll_widget = QWidget()
        self.setWidget(self.scroll_widget)
        self.vlayout = QVBoxLayout(self.scroll_widget)
        self.vlayout.setContentsMargins(30, 20, 30, 20)
        self.vlayout.setSpacing(15)

        header = QHBoxLayout()
        header.addWidget(SubtitleLabel("LLM 服务商配置", self))
        header.addStretch(1)
        self.add_btn = PrimaryPushButton(FIF.ADD, "添加服务商", self)
        self.add_btn.clicked.connect(self.add_provider_card)
        header.addWidget(self.add_btn)
        self.vlayout.addLayout(header)
        
        self.vlayout.addWidget(BodyLabel("配置 API Key 和 Base URL。这些设置将被模型和向量库引用。", self))
        self.vlayout.addSpacing(10)

        self.cards_layout = QVBoxLayout()
        self.vlayout.addLayout(self.cards_layout)
        self.vlayout.addStretch(1)
        
        self.cards = {}

    def load_data(self):
        while self.cards_layout.count():
            item = self.cards_layout.takeAt(0)
            if item.widget(): item.widget().deleteLater()
        self.cards = {}

        providers = ConfigData.get_providers()
        for key, data in providers.items():
            self._create_card(key, data)

    def _create_card(self, key, data):
        card = CardWidget(self.scroll_widget)
        layout = QVBoxLayout(card)
        
        h_layout = QHBoxLayout()
        key_edit = LineEdit()
        key_edit.setPlaceholderText("唯一标识 (如: silicon)")
        key_edit.setText(key)
        if key: key_edit.setReadOnly(True)
        
        del_btn = TransparentToolButton(FIF.DELETE, self)
        del_btn.clicked.connect(lambda: self._delete_card(key, card))
        
        h_layout.addWidget(StrongBodyLabel("ID:", self))
        h_layout.addWidget(key_edit)
        h_layout.addStretch(1)
        h_layout.addWidget(del_btn)
        layout.addLayout(h_layout)
        
        layout.addWidget(CaptionLabel("显示名称"))
        name_edit = LineEdit()
        name_edit.setText(data.get("name", ""))
        layout.addWidget(name_edit)

        layout.addWidget(CaptionLabel("Base URL"))
        url_edit = LineEdit()
        url_edit.setText(data.get("base_url", ""))
        url_edit.setPlaceholderText("https://api.example.com/v1")
        layout.addWidget(url_edit)

        layout.addWidget(CaptionLabel("API Key"))
        key_input = LineEdit()
        key_input.setText(data.get("api_key", ""))
        key_input.setEchoMode(LineEdit.EchoMode.Password)
        key_input.setPlaceholderText("sk-...")
        layout.addWidget(key_input)
        
        self.cards_layout.addWidget(card)
        
        self.cards[key] = {
            "widget": card,
            "key_edit": key_edit,
            "name_edit": name_edit,
            "url_edit": url_edit,
            "key_input": key_input
        }

    def add_provider_card(self):
        new_key = f"new_provider_{len(self.cards) + 1}"
        self._create_card(new_key, {"name": "New Provider"})
        self.cards[new_key]["key_edit"].setReadOnly(False)
        QApplication.processEvents()
        self.verticalScrollBar().setValue(self.verticalScrollBar().maximum())

    def _delete_card(self, key, card):
        card.deleteLater()
        if key in self.cards:
            del self.cards[key]

    def save_data(self):
        new_providers = {}
        for original_key, widgets in self.cards.items():
            final_key = widgets["key_edit"].text().strip()
            if not final_key: continue
            
            new_providers[final_key] = {
                "name": widgets["name_edit"].text(),
                "base_url": widgets["url_edit"].text(),
                "api_key": widgets["key_input"].text()
            }
        ConfigData.set_providers(new_providers)

# ============================================================================
# 界面 2: 向量配置
# ============================================================================
class VectorInterface(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("VectorInterface")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(30, 20, 30, 20)
        
        layout.addWidget(SubtitleLabel("RAG & 向量数据库设置", self))
        layout.addSpacing(10)
        
        self.card = CardWidget(self)
        c_layout = QVBoxLayout(self.card)
        
        c_layout.addWidget(StrongBodyLabel("Embedding 服务商"))
        self.combo_provider = ComboBox()
        c_layout.addWidget(self.combo_provider)
        c_layout.addSpacing(5)
        
        c_layout.addWidget(StrongBodyLabel("Embedding 模型名称"))
        self.line_embed = LineEdit()
        self.line_embed.setPlaceholderText("例如: BAAI/bge-m3")
        c_layout.addWidget(self.line_embed)
        c_layout.addSpacing(5)
        
        c_layout.addWidget(StrongBodyLabel("Rerank 模型名称"))
        self.line_rerank = LineEdit()
        self.line_rerank.setPlaceholderText("例如: BAAI/bge-reranker-v2-m3")
        c_layout.addWidget(self.line_rerank)
        
        layout.addWidget(self.card)
        layout.addStretch(1)

    def update_providers(self):
        current = self.combo_provider.text()
        self.combo_provider.clear()
        self.combo_provider.addItems(ConfigData.get_provider_keys())
        self.combo_provider.setCurrentText(current)

    def load_data(self):
        self.update_providers()
        vec = ConfigData.get_vector()
        self.combo_provider.setCurrentText(vec.get("provider", ""))
        self.line_embed.setText(vec.get("embedding_model", ""))
        self.line_rerank.setText(vec.get("rerank_model", ""))

    def save_data(self):
        ConfigData.set_vector({
            "provider": self.combo_provider.text(),
            "embedding_model": self.line_embed.text(),
            "rerank_model": self.line_rerank.text()
        })

# ============================================================================
# 界面 3: 角色配置
# ============================================================================
class RoleInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("RoleInterface")
        
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        self.list_widget = QListWidget()
        self.list_widget.setFixedWidth(220)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.setStyleSheet("""
            QListWidget { background-color: transparent; border: none; outline: none; }
            QListWidget::item { padding: 12px; border-radius: 5px; }
            QListWidget::item:selected { background-color: rgba(255, 255, 255, 0.1); }
        """)
        
        self.edit_area = QScrollArea()
        self.edit_area.setWidgetResizable(True)
        self.edit_area.setStyleSheet("QScrollArea {background: transparent; border: none;}")
        
        self.edit_widget = QWidget()
        self.edit_area.setWidget(self.edit_widget)
        self.form_layout = QVBoxLayout(self.edit_widget)
        self.form_layout.setContentsMargins(20, 20, 20, 20)
        self.form_layout.setSpacing(10)
        
        self.lbl_key = SubtitleLabel("Select a Role")
        self.line_name = LineEdit()
        self.combo_prov = ComboBox()
        self.line_model = LineEdit()
        self.spin_temp = DoubleSpinBox()
        self.spin_temp.setRange(0.0, 2.0)
        self.spin_temp.setSingleStep(0.1)
        self.text_prompt = TextEdit()
        self.text_prompt.setMinimumHeight(300)
        
        self.form_layout.addWidget(self.lbl_key)
        self.form_layout.addSpacing(10)
        self.form_layout.addWidget(CaptionLabel("角色显示名称"))
        self.form_layout.addWidget(self.line_name)
        
        h_layout = QHBoxLayout()
        v1 = QVBoxLayout()
        v1.addWidget(CaptionLabel("服务商"))
        v1.addWidget(self.combo_prov)
        h_layout.addLayout(v1)
        
        v2 = QVBoxLayout()
        v2.addWidget(CaptionLabel("模型 ID"))
        v2.addWidget(self.line_model)
        h_layout.addLayout(v2)
        
        v3 = QVBoxLayout()
        v3.addWidget(CaptionLabel("温度 (Temperature)"))
        v3.addWidget(self.spin_temp)
        h_layout.addLayout(v3)
        
        self.form_layout.addLayout(h_layout)
        self.form_layout.addWidget(CaptionLabel("系统提示词 (System Prompt)"))
        self.form_layout.addWidget(self.text_prompt)
        self.form_layout.addStretch(1)

        layout.addWidget(self.list_widget)
        layout.addWidget(self.edit_area)
        
        self.current_role_idx = -1
        self.roles_data = []

    def load_data(self):
        self.roles_data = deepcopy(ConfigData.get_roles())
        self.list_widget.clear()
        
        providers = ConfigData.get_provider_keys()
        self.combo_prov.clear()
        self.combo_prov.addItems(providers)
        
        for role in self.roles_data:
            item = QListWidgetItem(f"{role.get('name')} ({role.get('key')})")
            self.list_widget.addItem(item)
            
        if self.roles_data:
            self.list_widget.setCurrentRow(0)
            self._on_item_clicked(self.list_widget.item(0))

    def _save_current_to_memory(self):
        if self.current_role_idx >= 0 and self.current_role_idx < len(self.roles_data):
            role = self.roles_data[self.current_role_idx]
            role['name'] = self.line_name.text()
            role['provider'] = self.combo_prov.text()
            role['model'] = self.line_model.text()
            role['temperature'] = self.spin_temp.value()
            role['prompt'] = self.text_prompt.toPlainText()

    def _on_item_clicked(self, item):
        self._save_current_to_memory()
        idx = self.list_widget.row(item)
        self.current_role_idx = idx
        role = self.roles_data[idx]
        
        self.lbl_key.setText(f"配置角色: {role.get('key')}")
        self.line_name.setText(role.get('name', ''))
        self.combo_prov.setCurrentText(role.get('provider', ''))
        self.line_model.setText(role.get('model', ''))
        self.spin_temp.setValue(role.get('temperature', 0.7))
        self.text_prompt.setPlainText(role.get('prompt', ''))

    def save_data(self):
        self._save_current_to_memory()
        ConfigData.set_roles(self.roles_data)

# ============================================================================
# 界面 4: 预设清洗 (Ingest)
# ============================================================================
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

class IngestInterface(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("IngestInterface")
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(30, 20, 30, 20)
        self.layout.setSpacing(15)

        self.layout.addWidget(SubtitleLabel("SillyTavern 预设清洗入库", self))
        self.layout.addWidget(BodyLabel("将世界书或预设文件清洗为向量数据库规则，供游戏内 RAG 使用。", self))
        
        # 1. 文件选择
        file_card = CardWidget(self)
        file_layout = QHBoxLayout(file_card)
        
        self.path_edit = LineEdit()
        self.path_edit.setPlaceholderText("请选择 .json 文件...")
        self.browse_btn = PrimaryPushButton("浏览", self)
        self.browse_btn.clicked.connect(self.browse_file)
        
        file_layout.addWidget(self.path_edit)
        file_layout.addWidget(self.browse_btn)
        self.layout.addWidget(file_card)

        # 2. LLM 配置
        self.layout.addWidget(StrongBodyLabel("清洗用 LLM 配置 (建议使用高智商模型)", self))
        config_card = CardWidget(self)
        config_layout = QVBoxLayout(config_card)
        
        config_layout.addWidget(CaptionLabel("Base URL"))
        self.url_edit = LineEdit()
        self.url_edit.setText("https://api.siliconflow.cn/v1")
        config_layout.addWidget(self.url_edit)
        
        config_layout.addWidget(CaptionLabel("API Key"))
        self.key_edit = LineEdit()
        self.key_edit.setPlaceholderText("sk-...")
        self.key_edit.setEchoMode(LineEdit.EchoMode.Password)
        config_layout.addWidget(self.key_edit)
        
        config_layout.addWidget(CaptionLabel("模型名称"))
        self.model_edit = LineEdit()
        self.model_edit.setText("deepseek-ai/DeepSeek-V3")
        config_layout.addWidget(self.model_edit)
        
        self.layout.addWidget(config_card)

        # 3. 开始按钮
        action_layout = QHBoxLayout()
        self.start_btn = PrimaryPushButton("开始清洗入库", self)
        self.start_btn.isSelectable = False # 防止报错
        self.start_btn.clicked.connect(self.start_ingest)
        self.start_btn.setFixedWidth(200)
        action_layout.addStretch(1)
        action_layout.addWidget(self.start_btn)
        self.layout.addLayout(action_layout)

        # 4. 日志
        self.layout.addWidget(StrongBodyLabel("执行日志", self))
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

        self.start_btn.setEnabled(False)
        self.start_btn.setText("正在清洗中...")
        self.log_view.clear()

        self.worker = IngestWorker(path, llm_config)
        self.worker.log_signal.connect(self.append_log)
        self.worker.finished_signal.connect(self.on_finished)
        self.worker.start()

    def append_log(self, text):
        self.log_view.append(text)
        self.log_view.verticalScrollBar().setValue(
            self.log_view.verticalScrollBar().maximum()
        )

    def on_finished(self):
        self.start_btn.setEnabled(True)
        self.start_btn.setText("开始清洗入库")
        InfoBar.success("完成", "清洗任务已结束，请查看日志。", parent=self)


# ============================================================================
# 主窗口
# ============================================================================
class ConfigEditorWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("DeepTavern 控制中心")
        self.resize(1100, 750)
        self.setWindowIcon(FIF.SETTING.icon())

        ConfigData.load()

        # 初始化子页面
        self.provider_interface = ProviderInterface(self)
        self.vector_interface = VectorInterface(self)
        self.role_interface = RoleInterface(self)
        self.ingest_interface = IngestInterface(self)

        # 导航栏
        self.addSubInterface(self.provider_interface, FIF.ALBUM, "服务商配置")
        self.addSubInterface(self.vector_interface, FIF.SEARCH, "向量与 RAG")
        self.addSubInterface(self.role_interface, FIF.PEOPLE, "角色模型分配")
        self.addSubInterface(self.ingest_interface, FIF.SYNC, "预设清洗工具")

        # 底部保存按钮
        self.save_btn = PrimaryPushButton("保存所有配置", self)
        self.save_btn.isSelectable = False
        self.save_btn.clicked.connect(self.save_all)
        self.save_btn.setFixedWidth(200)
        
        self.navigationInterface.addWidget(
            routeKey="save_btn",
            widget=self.save_btn,
            onClick=self.save_all,
            position=NavigationItemPosition.BOTTOM
        )
        
        # 初始加载
        self.provider_interface.load_data()
        self.vector_interface.load_data()
        self.role_interface.load_data()
        
        self.stackedWidget.currentChanged.connect(self.on_tab_changed)

    def on_tab_changed(self, index):
        if self.stackedWidget.currentWidget() == self.provider_interface:
            self.provider_interface.save_data()
        
        self.vector_interface.update_providers()
        
        current_prov = self.role_interface.combo_prov.text()
        self.role_interface.combo_prov.clear()
        self.role_interface.combo_prov.addItems(ConfigData.get_provider_keys())
        self.role_interface.combo_prov.setCurrentText(current_prov)

    def save_all(self):
        self.provider_interface.save_data()
        self.vector_interface.save_data()
        self.role_interface.save_data()
        
        if ConfigData.save():
            InfoBar.success(
                title='保存成功',
                content=f"配置已写入 {CONFIG_FILE}",
                orient=Qt.Orientation.Horizontal,
                isClosable=True,
                position=InfoBarPosition.TOP_RIGHT,
                duration=2000,
                parent=self
            )
        else:
            InfoBar.error(
                title='保存失败',
                content="写入文件时发生错误，请检查权限。",
                parent=self
            )

if __name__ == '__main__':
    app = QApplication(sys.argv)
    w = ConfigEditorWindow()
    w.show()
    sys.exit(app.exec())
