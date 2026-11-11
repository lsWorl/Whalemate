"""“自己的生活”（计划书 v4 功能四）：开关、每天做几件，以及她最近看了什么、怎么想的、有没有跟你说、你回了没有。"""
from __future__ import annotations

from datetime import datetime
from html import escape

from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from aicompanion.brain.life import DAILY, LifeService

STATUS = {'doing': '正在看', 'done': '看完了', 'failed': '没看成', 'shared': '跟你说了', 'cancelled': '你关掉了，没看完', 'quiet': '看完了（你关掉了，不拿来说）'}
RESPONSE = {'answered': '你回了', 'ignored': '你没回'}


class LifeCard(QWidget):
    def __init__(self, database, character_id: str, parent=None):
        super().__init__(parent)
        self.db, self.cid, self.life = database, character_id, LifeService(database)
        box = QVBoxLayout(self); box.setContentsMargins(0, 0, 0, 0)
        self.enabled = QCheckBox('允许她空闲时按兴趣去网上看点东西，遇到有意思的来找你分享')
        self.enabled.setChecked(self.life.enabled())
        self.daily = QSpinBox(); self.daily.setRange(0, 10); self.daily.setSuffix(' 件'); self.daily.setValue(self.life.daily())
        row = QHBoxLayout(); row.addWidget(QLabel('每天最多做')); row.addWidget(self.daily); row.addStretch(1)
        hint = QLabel('她的兴趣在“人格”页里改。每件事会用一次联网搜索（和查梗共用每天的搜索次数）和一次小的模型调用；'
                      '分享时只说搜到的内容并说明出处，不编。')
        hint.setObjectName('Hint'); hint.setWordWrap(True)
        self.recent = QLabel(); self.recent.setWordWrap(True); self.recent.setOpenExternalLinks(True)
        for widget in (self.enabled,): box.addWidget(widget)
        box.addLayout(row); box.addWidget(hint); box.addWidget(QLabel('她最近在做的事')); box.addWidget(self.recent)
        self.refresh()

    def refresh(self):
        rows = self.life.recent(self.cid, 8)
        if not rows:
            self.recent.setText('还没有。她空闲的时候会自己去看看。'); return
        lines = []
        for r in rows:
            when = datetime.fromtimestamp(r['started_at']).strftime('%m-%d %H:%M')
            state = STATUS.get(r['status'], r['status'])
            if r['response']: state += '，' + RESPONSE.get(r['response'], r['response'])
            what = (f'<a href="{escape(r["url"], quote=True)}">{escape(r["title"])}</a>（{escape(r["site"] or "网页")}）'
                    if r['url'].startswith('https://') or r['url'].startswith('http://') else escape(r['title'] or '—'))
            thought = f'<br>　她觉得：{escape(r["thought"])}' if r['thought'] else ''
            lines.append(f'{when} · {escape(r["interest"])} · {state}<br>　{what}{thought}')
        self.recent.setText('<br>'.join(lines))

    def save(self):
        self.db.set_setting('life_enabled', self.enabled.isChecked())
        self.db.set_setting('life_daily_activities', self.daily.value())
