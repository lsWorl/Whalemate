"""设置 → 关系与心情：她现在的心情、为什么，以及手动“哄好”（计划书 v4 功能三）。"""
from __future__ import annotations

import time

from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QLabel, QProgressBar, QPushButton, QVBoxLayout, QWidget

from aicompanion.domain.mood import LABELS, MOODS
from aicompanion.services.mood_service import MoodService


class MoodCard(QWidget):
    def __init__(self, database, character_id, parent=None, clock=time.time):
        super().__init__(parent)
        self.service = MoodService(database, clock=clock); self.cid = character_id; self.clock = clock
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.summary = QLabel(); self.summary.setWordWrap(True); box.addWidget(self.summary)
        grid = QGridLayout(); self.bars = {}
        for row, mood in enumerate(MOODS):
            bar = QProgressBar(); bar.setRange(0, 100); bar.setTextVisible(False); bar.setFixedHeight(8)
            grid.addWidget(QLabel(LABELS[mood]), row, 0); grid.addWidget(bar, row, 1); self.bars[mood] = bar
        box.addLayout(grid)
        self.events = QLabel(); self.events.setObjectName('Hint'); self.events.setWordWrap(True); box.addWidget(self.events)
        row = QHBoxLayout(); row.addStretch(1)
        refresh = QPushButton('刷新'); refresh.clicked.connect(self.refresh); row.addWidget(refresh)
        self.soothe_button = QPushButton('哄好她'); self.soothe_button.setToolTip('委屈和生气马上清零（开心、撒娇不变）')
        self.soothe_button.clicked.connect(self.soothe); row.addWidget(self.soothe_button)
        box.addLayout(row)
        self.refresh()

    def refresh(self):
        mood = self.service.current(self.cid); label = mood.label()
        reason = self.service.reason(self.cid, mood)
        text = '现在挺平静的。' if label == 'calm' else f"现在{'很' if mood.intensity() >= 65 else '有点'}{LABELS[label]}" + (f'，因为{reason}。' if reason else '。')
        self.summary.setText(text)
        for name, bar in self.bars.items(): bar.setValue(int(mood.levels.get(name, 0)))
        self.soothe_button.setEnabled(any(mood.levels.get(m, 0) >= 1 for m in ('sad', 'angry')))
        lines = []
        for event in self.service.recent(self.cid, 6):
            ago = max(0, self.clock()-event['at'])
            when = '刚刚' if ago < 120 else (f'{int(ago//60)} 分钟前' if ago < 3600 else (f'{int(ago//3600)} 小时前' if ago < 86400 else f'{int(ago//86400)} 天前'))
            lines.append(f"{when}：{event['label']}" + (f"（{event['reason']}）" if event['reason'] and event['reason'] != event['label'] else ''))
        self.events.setText('最近：\n'+'\n'.join(lines) if lines else '最近没有影响心情的事。')

    def soothe(self):
        self.service.soothe(self.cid); self.refresh()
