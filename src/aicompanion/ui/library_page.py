"""图库沿用聊天平台路径；耗时扫描/导入在后台，审核和启停直接保存。"""
import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon,QPixmap
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QCheckBox,
 QTableWidget,QTableWidgetItem,QLineEdit,QComboBox,QPlainTextEdit,QFileDialog,QMessageBox,QApplication)
from aicompanion.expression.catalog import AssetCatalog,LICENSES
from aicompanion.ui.chat_worker import TaskWorker

class LibraryPage(QWidget):
    def __init__(self,database,character_id,parent=None):
        super().__init__(parent);self.catalog=AssetCatalog(database);self.character_id=character_id;self.worker=None
        layout=QVBoxLayout(self)
        hint=QLabel('沿用“聊天平台”的表情包目录。导入不会覆盖原图；未知许可先隔离。作者和来源应填写实际授权证据。')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        row=QHBoxLayout()
        for label,fn in [('刷新图库索引',self.scan),('导入图片',self.pick_import),('导入素材清单',self.pick_pack),('启用 / 禁用',self.toggle),('清理缩略图缓存',self.clear_cache)]:
            button=QPushButton(label);button.clicked.connect(fn);row.addWidget(button)
        layout.addLayout(row)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['预览 / 分类','含义','审核 / 启用','许可','最近使用'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.itemSelectionChanged.connect(self.select);layout.addWidget(self.table)
        form=QFormLayout();self.semantic=QLineEdit();self.intents=QLineEdit();self.emotions=QLineEdit()
        self.author=QLineEdit();self.source=QLineEdit();self.license=QComboBox();self.license.addItems(['unknown']+list(LICENSES))
        for label,field in [('表情含义',self.semantic),('表达意图（逗号分隔）',self.intents),('情绪（逗号分隔）',self.emotions),
                            ('原作者',self.author),('来源 / 授权证据',self.source),('许可证',self.license)]:form.addRow(label,field)
        layout.addLayout(form)
        approve=QPushButton('保存标注并确认许可、启用选中图片');approve.clicked.connect(self.approve);layout.addWidget(approve)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(105);layout.addWidget(self.details)
        self.status=QLabel();self.status.setWordWrap(True);layout.addWidget(self.status)
        # 最近几轮"发没发表情、卡在哪一步"：没发图时能直接看出是没候选、模型选了文字、格式没解析、图片校验失败还是显示 / 发送失败
        decisions=QHBoxLayout();decisions.addWidget(QLabel('最近的表达决策（不含聊天内容）'))
        self.debug=QCheckBox('开发模式：同时写入日志');self.debug.setChecked(bool(database.get_setting('expression_debug',False)))
        self.debug.toggled.connect(lambda on:database.set_setting('expression_debug',bool(on)));decisions.addWidget(self.debug)
        again=QPushButton('刷新');again.clicked.connect(self.show_traces);decisions.addWidget(again);layout.addLayout(decisions)
        self.trace_view=QPlainTextEdit();self.trace_view.setReadOnly(True);self.trace_view.setMaximumHeight(120);layout.addWidget(self.trace_view)
        self.refresh()
    def selected(self):
        row=self.table.currentRow()
        return self.table.item(row,0).data(Qt.ItemDataRole.UserRole) if row>=0 and self.table.item(row,0) else None
    def refresh(self):
        rows=self.catalog.rows();self.table.setRowCount(len(rows))
        with self.catalog.db.transaction() as c:
            last={r['asset_id']:r['used'] for r in c.execute("SELECT asset_id,MAX(updated_at) AS used FROM expression_deliveries WHERE status IN ('displayed','accepted') GROUP BY asset_id")}
        for n,row in enumerate(rows):
            m=row['metadata'];item=QTableWidgetItem(m.get('category',row['asset_id']))
            item.setData(Qt.ItemDataRole.UserRole,row['asset_id'])
            if row['thumbnail']:
                pix=QPixmap();pix.loadFromData(row['thumbnail']);item.setIcon(QIcon(pix))
            self.table.setItem(n,0,item)
            values=[m.get('semantic',''),'已审核' if row['review_status']=='approved' else '待审核',
                    m.get('license','unknown'),'已使用' if row['asset_id'] in last else '未使用']
            values[1]+=' / '+('启用' if row['enabled'] else '停用')
            for col,value in enumerate(values,1):self.table.setItem(n,col,QTableWidgetItem(str(value)))
        self.show_traces()
        stats=self.catalog.stats()
        self.summary.setText(f"图库：{self.catalog.root}\n共 {stats['total']} 张，已审核 {stats['approved']}，待审核 {stats['pending']}；缩略图缓存 {stats['thumbnail_bytes']//1024} KiB")
        self.table.resizeColumnsToContents()
    def show_traces(self):
        from aicompanion.expression.service import ExpressionService
        import time
        lines=[time.strftime('%m-%d %H:%M',time.localtime(e.get('at',0)))+'  '+ExpressionService.describe(e)
               for e in reversed(ExpressionService(self.catalog.db).traces(20))]
        self.trace_view.setPlainText('\n'.join(lines) or '还没有记录。聊几句后点"刷新"。')

    def select(self):
        row=self.catalog.get(self.selected()) if self.selected() else None
        if not row:return
        m=row['metadata'];self.semantic.setText(m.get('semantic',''));self.intents.setText(','.join(m.get('intents',[])))
        self.emotions.setText(','.join(m.get('emotions',[])));self.author.setText(m.get('author',''));self.source.setText(m.get('source',''))
        self.license.setCurrentText(m.get('license','unknown'))
        details={k:v for k,v in row.items() if k not in ('thumbnail',)}
        self.details.setPlainText(json.dumps(details,ensure_ascii=False,indent=2))
    def metadata(self):
        return dict(character_id=self.character_id,semantic=self.semantic.text()[:160],
                    intents=[x.strip()[:40] for x in self.intents.text().replace('，',',').split(',') if x.strip()][:8],
                    emotions=[x.strip()[:40] for x in self.emotions.text().replace('，',',').split(',') if x.strip()][:8],
                    author=self.author.text().strip(),source=self.source.text().strip(),license=self.license.currentText())
    def background(self,fn):
        if self.worker and self.worker.isRunning():self.status.setText('正在处理，请稍候');return
        owner=self.window().parentWidget()
        worker=TaskWorker(fn,owner or QApplication.instance());self.worker=worker
        if owner is not None and hasattr(owner,'tasks'):
            owner.tasks.add(worker);worker.finished.connect(lambda:owner._task_finished(worker))
        else:worker.finished.connect(worker.deleteLater)
        worker.succeeded.connect(self.done);worker.failed.connect(lambda _:self.status.setText('处理失败：图片、目录或来源不符合要求；原文件保留'))
        worker.start();self.status.setText('正在后台校验图片…')
    def done(self,value):
        self.refresh();self.status.setText('处理完成：'+str(value))
    def scan(self):
        self.background(lambda token:self.catalog.scan(token))
    def pick_import(self):
        path,_=QFileDialog.getOpenFileName(self,'导入表情包','','图片 (*.png *.jpg *.jpeg *.gif *.webp)')
        if path:
            metadata=self.metadata();self.background(lambda token:self.catalog.import_image(path,metadata))
    def pick_pack(self):
        path,_=QFileDialog.getOpenFileName(self,'导入已下载的素材清单','','清单 (*.json)')
        if path:self.background(lambda token:self.catalog.import_pack(path,token))
    def approve(self):
        aid=self.selected()
        if not aid:return
        try:
            metadata=self.metadata()
            self.catalog.review(aid,**{k:metadata[k] for k in ('author','source','license')})
            metadata.pop('character_id',None)
            self.catalog.annotate(aid,metadata);self.refresh();self.status.setText('已保存标注和许可确认')
        except ValueError as error:self.status.setText(str(error))
    def toggle(self):
        aid=self.selected();row=self.catalog.get(aid) if aid else None
        if row:
            self.catalog.enable(aid,not row['enabled']);self.refresh()
            self.status.setText('已更新；待审核图片不能启用')
    def clear_cache(self):
        self.catalog.clear_thumbnails();self.refresh();self.status.setText('仅清理索引内缩略图；原图和审核记录保留，刷新可重建')
