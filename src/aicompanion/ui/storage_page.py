"""可选择磁盘的记忆 / 表情位置，记忆迁移留到旧实例退出后的下一次启动。"""
from pathlib import Path

from PySide6.QtWidgets import QWidget, QVBoxLayout, QFormLayout, QHBoxLayout, QLineEdit, QLabel, QPushButton, QFileDialog

from aicompanion.storage.locations import default_data_root, record_path, plan_change, seed_memes, read_record, write_record


class StoragePage(QWidget):
    def __init__(self, database, settings, parent=None):
        super().__init__(parent)
        self.database, self.settings = database, settings
        self.path_file = settings.storage_paths_file or record_path()
        default = default_data_root()
        self.current_database = Path(database.database_path).resolve()
        current_memes = database.get_setting('qq_meme_root', '') or str(settings.meme_directory or default/'memes')
        pending = read_record(self.path_file).get('migration') if settings.storage_paths_file else None
        memory = str(Path(pending['target']).parent) if pending else str(self.current_database.parent)
        self.loaded = (memory, current_memes)
        self.default_memes = str(settings.meme_directory or default/'memes')
        layout = QVBoxLayout(self)
        hint = QLabel('聊天、记忆、心事和表达习惯保存在同一个数据库中。修改位置后，下次启动会复制到新目录；旧文件保留，不覆盖目标已有的数据库。')
        hint.setWordWrap(True); layout.addWidget(hint)
        form = QFormLayout()
        self.memory = QLineEdit(self.loaded[0]); self.memes = QLineEdit(self.loaded[1])
        for label, field in (('记忆和聊天目录', self.memory), ('表情包目录', self.memes)):
            row = QHBoxLayout(); row.addWidget(field, 1)
            choose = QPushButton('选择磁盘 / 目录…')
            choose.clicked.connect(lambda checked=False, widget=field: self._choose(widget))
            row.addWidget(choose); form.addRow(label, row)
        layout.addLayout(form)
        current = QLabel('当前数据库：'+str(self.current_database)); current.setWordWrap(True); layout.addWidget(current)
        recommendation = QPushButton('使用项目盘 / 非系统盘的推荐目录')
        recommendation.clicked.connect(lambda: (self.memory.setText(str(default)), self.memes.setText(str(default/'memes'))))
        layout.addWidget(recommendation)
        self.status = QLabel('选择 D 盘或其他非系统盘，点下方“保存”，再从托盘彻底退出并重开桌宠。表情目录可选已有分类图库；空目录会补入自带鲸娘表情。')
        self.status.setWordWrap(True); layout.addWidget(self.status); layout.addStretch(1)

    def _choose(self, field):
        folder = QFileDialog.getExistingDirectory(self, '选择非系统盘上的存放目录', field.text())
        if folder: field.setText(folder)

    def save(self):
        memory, memes = self.memory.text().strip(), self.memes.text().strip()
        actual_memes = self.database.get_setting('qq_meme_root', '') or self.default_memes
        if (memory, memes) == self.loaded and actual_memes == self.loaded[1]: return False
        # 聊天平台页也能选图库：若本页没有改图库，保留刚在另一页保存的选择。
        if memes == self.loaded[1]: memes = actual_memes
        result = plan_change(self.current_database, memory, memes, self.path_file, persist=False)
        seed_memes(result['meme_root'])
        before = self.database.get_setting('qq_meme_root', '')
        self.database.set_setting('qq_meme_root', result['meme_root'])
        try: write_record(self.path_file, result)
        except Exception:
            self.database.set_setting('qq_meme_root', before)
            raise
        self.status.setText('新目录已保存。请从托盘退出并重开；下次启动复制记忆，旧数据库保留。')
        self.loaded = (memory, memes)
        return True
