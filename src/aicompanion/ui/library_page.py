"""图库沿用聊天平台路径；耗时扫描/导入/识别在后台，启停和来源可批量修改并直接保存。

负责人 2026-11-11：自己放进图库或手动导入的图默认当作“自己的图（自用）”直接可用；含义由识图模型自动识别，
不要求用户填写作者、来源和含义。素材清单导入的图仍按清单写明的许可，没写的标成“许可待确认”。"""
import json
from PySide6.QtCore import Qt
from PySide6.QtGui import QIcon,QPixmap
from PySide6.QtWidgets import (QWidget,QVBoxLayout,QHBoxLayout,QFormLayout,QLabel,QPushButton,QCheckBox,
 QTableWidget,QTableWidgetItem,QLineEdit,QComboBox,QPlainTextEdit,QFileDialog,QMessageBox,QApplication,QSpinBox)
from aicompanion.expression.catalog import AssetCatalog,LICENSES,LICENSE_LABELS
from aicompanion.ui.chat_worker import TaskWorker

class LibraryPage(QWidget):
    ANALYZE_BATCH=10
    def __init__(self,database,character_id,parent=None,controller=None):
        super().__init__(parent);self.catalog=AssetCatalog(database);self.character_id=character_id;self.worker=None
        self.controller=controller;self.analyzer=None
        layout=QVBoxLayout(self)
        hint=QLabel('沿用“聊天平台”的表情包目录。放进目录或导入的图默认当作你自己的图，直接可用；含义会用识图模型自动识别。'
                   '素材清单里没写许可的图标为“许可待确认”，选中后在“来源”里选一项即可启用。')
        hint.setWordWrap(True);layout.addWidget(hint)
        self.summary=QLabel();self.summary.setWordWrap(True);layout.addWidget(self.summary)
        # 收集 QQ 里别人发的表情包（用户 2026-11-09）：存进图库"收集的表情"目录，识图分析含义后直接可用
        collect=QHBoxLayout()
        self.collect=QCheckBox('收集 QQ 私聊和群里别人发的表情包（分析含义后她也能用）')
        self.collect.setChecked(bool(database.get_setting('sticker_collect_enabled',True)))
        self.collect.toggled.connect(lambda on:database.set_setting('sticker_collect_enabled',bool(on)));collect.addWidget(self.collect)
        collect.addWidget(QLabel('每天最多分析'))
        self.analysis_daily=QSpinBox();self.analysis_daily.setRange(0,1000);self.analysis_daily.setSuffix(' 张')
        from aicompanion.expression.collect import ANALYSIS_DAILY
        self.analysis_daily.setValue(int(database.get_setting('sticker_analysis_daily',ANALYSIS_DAILY) or 0))
        self.analysis_daily.valueChanged.connect(lambda value:database.set_setting('sticker_analysis_daily',int(value)))
        collect.addWidget(self.analysis_daily);collect.addStretch(1);layout.addLayout(collect)
        self.collect_status=QLabel();self.collect_status.setWordWrap(True);layout.addWidget(self.collect_status)
        row=QHBoxLayout()
        for label,fn in [('刷新图库',self.scan),('导入图片',self.pick_import),('导入素材清单',self.pick_pack),('清理缩略图缓存',self.clear_cache)]:
            button=QPushButton(label);button.clicked.connect(fn);row.addWidget(button)
        layout.addLayout(row)
        self.table=QTableWidget(0,5);self.table.setHorizontalHeaderLabels(['预览 / 分类','含义','状态','来源','最近使用'])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers);self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        # 可多选（Ctrl / Shift 点选，或“全选”），下面的按钮对所有选中的图生效
        self.table.setSelectionMode(QTableWidget.SelectionMode.ExtendedSelection)
        self.table.itemSelectionChanged.connect(self.select);layout.addWidget(self.table)
        batch=QHBoxLayout()
        for label,fn in [('全选',self.table.selectAll),('启用选中',lambda:self.enable_selected(True)),('停用选中',lambda:self.enable_selected(False)),
                         ('重新识别含义',self.reanalyze)]:
            button=QPushButton(label);button.clicked.connect(fn);batch.addWidget(button)
        self.license=QComboBox()
        for value in LICENSES:self.license.addItem(LICENSE_LABELS.get(value,value),value)
        batch.addWidget(QLabel('来源'));batch.addWidget(self.license)
        apply=QPushButton('设为选中图片的来源');apply.clicked.connect(self.set_license);batch.addWidget(apply)
        batch.addStretch(1);layout.addLayout(batch)
        meaning=QHBoxLayout();meaning.addWidget(QLabel('含义（自动识别，一般不用改）'))
        self.semantic=QLineEdit();self.semantic.setPlaceholderText('选中一张图后可以改；留空保存 = 重新自动识别');meaning.addWidget(self.semantic,1)
        save=QPushButton('保存含义');save.clicked.connect(self.save_meaning);meaning.addWidget(save);layout.addLayout(meaning)
        self.details=QPlainTextEdit();self.details.setReadOnly(True);self.details.setMaximumHeight(90);layout.addWidget(self.details)
        self.status=QLabel();self.status.setWordWrap(True);self.status.setTextFormat(Qt.TextFormat.PlainText);layout.addWidget(self.status)
        # 最近几轮"发没发表情、卡在哪一步"：没发图时能直接看出是没候选、模型选了文字、格式没解析、图片校验失败还是显示 / 发送失败
        decisions=QHBoxLayout();decisions.addWidget(QLabel('最近的表达决策（不含聊天内容）'))
        self.debug=QCheckBox('开发模式：同时写入日志');self.debug.setChecked(bool(database.get_setting('expression_debug',False)))
        self.debug.toggled.connect(lambda on:database.set_setting('expression_debug',bool(on)));decisions.addWidget(self.debug)
        again=QPushButton('刷新');again.clicked.connect(self.show_traces);decisions.addWidget(again);layout.addLayout(decisions)
        self.trace_view=QPlainTextEdit();self.trace_view.setReadOnly(True);self.trace_view.setMaximumHeight(120);layout.addWidget(self.trace_view)
        self.refresh()
        self.analyze_soon()   # 打开图库页时顺手识别还没识别含义的图（配置了识图模型才会）
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
            values=[m.get('semantic',''),self.state(row),LICENSE_LABELS.get(m.get('license','unknown'),m.get('license','unknown')),
                    '已使用' if row['asset_id'] in last else '未使用']
            for col,value in enumerate(values,1):self.table.setItem(n,col,QTableWidgetItem(str(value)))
        self.show_traces()
        stats=self.catalog.stats()
        waiting=sum(1 for r in rows if r['metadata'].get('analysis')=='pending')
        self.summary.setText(f"图库：{self.catalog.root}\n共 {stats['total']} 张，可用 {sum(1 for r in rows if r['enabled'] and r['review_status']=='approved')}"
                             +(f"，许可待确认 {stats['pending']}" if stats['pending'] else '')
                             +(f"，等识别含义 {waiting}（{self.analysis_hint()}）" if waiting else '')
                             +f"；缩略图缓存 {stats['thumbnail_bytes']//1024} KiB")
        from aicompanion.expression.collect import StickerCollector
        collector=StickerCollector(self.catalog.db,self.catalog);c=collector.stats()
        self.collect_status.setText('还没选表情包目录（"聊天平台"页），收集功能暂不工作。' if collector.root() is None else
            f"收集来的表情：{c['collected']} 张（可用 {c['ready']}，等分析 {c['pending']}，不适合或已停用 {c['unsuitable']}，分析失败 {c['failed']}）；今天已分析 {c['analyzed_today']} 张")
        # 最近收到的表情包怎么处理的（排查"收不到"）：没有记录 = 插件根本没把表情包传过来（多半是没认出是表情）
        import time
        names={'collected':'新收','known':'已有','no_library':'没选图库','disabled':'收集已关','full':'已满','daily_limit':'今天收满',
               'invalid':'图片无效','bad_image':'图片无效','unknown_hash':'插件记得但图库里没有','not_allowed':'不在允许范围','error':'出错'}
        intake=[e for e in (self.catalog.db.get_setting('sticker_intake',[]) or []) if isinstance(e,dict)][-5:]
        if intake:
            lines=[time.strftime('%H:%M',time.localtime(e.get('at',0)))+(' 群 ' if e.get('kind')=='group' else ' 私聊 ')
                   +'、'.join(names.get(x,x or '?') for x in e.get('status',[])) for e in reversed(intake)]
            self.collect_status.setText(self.collect_status.text()+'\n最近收到的表情：'+'；'.join(lines))
        else:
            self.collect_status.setText(self.collect_status.text()+'\n最近还没收到插件传来的表情包。')
        self.table.resizeColumnsToContents()
    def show_traces(self):
        from aicompanion.expression.service import ExpressionService
        import time
        lines=[time.strftime('%m-%d %H:%M',time.localtime(e.get('at',0)))+'  '+ExpressionService.describe(e)
               for e in reversed(ExpressionService(self.catalog.db).traces(20))]
        self.trace_view.setPlainText('\n'.join(lines) or '还没有记录。聊几句后点"刷新"。')

    @staticmethod
    def state(row):
        m=row['metadata']
        if row['review_status']!='approved':return '许可待确认'
        if not row['enabled']:return '停用'+('（不适合发）' if m.get('suitable') is False else '')
        if m.get('analysis')=='pending':return '可用 · 识别含义中'
        if m.get('analysis')=='failed':return '可用 · 含义没识别出来（可点“重新识别含义”）'
        return '可用'
    def selected_ids(self):
        rows=sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.table.item(r,0).data(Qt.ItemDataRole.UserRole) for r in rows if self.table.item(r,0)]
    def selected(self):
        ids=self.selected_ids();return ids[0] if len(ids)==1 else None
    def select(self):
        ids=self.selected_ids()
        if len(ids)!=1:
            self.semantic.clear();self.details.setPlainText(f'已选中 {len(ids)} 张' if ids else '');return
        row=self.catalog.get(ids[0])
        if not row:return
        m=row['metadata'];self.semantic.setText(m.get('semantic',''))
        index=self.license.findData(m.get('license'))
        if index>=0:self.license.setCurrentIndex(index)
        details={k:v for k,v in row.items() if k not in ('thumbnail',)}
        self.details.setPlainText(json.dumps(details,ensure_ascii=False,indent=2))
    def metadata(self):
        return dict(character_id=self.character_id)
    def keep_selection(self,ids):
        """刷新后重新选中刚才操作的图，方便连续批量操作。"""
        self.table.blockSignals(True);self.table.clearSelection()
        from PySide6.QtCore import QItemSelectionModel
        for r in range(self.table.rowCount()):
            item=self.table.item(r,0)
            if item and item.data(Qt.ItemDataRole.UserRole) in ids:
                self.table.selectionModel().select(self.table.model().index(r,0),
                    QItemSelectionModel.SelectionFlag.Select|QItemSelectionModel.SelectionFlag.Rows)
        self.table.blockSignals(False);self.select()
    def enable_selected(self,enabled):
        ids=self.selected_ids()
        if not ids:self.status.setText('先在表格里选中图片（可按住 Ctrl / Shift 多选，或点“全选”）');return
        pending=0
        for aid in ids:
            row=self.catalog.get(aid)
            if row and row['review_status']!='approved':pending+=1
            self.catalog.enable(aid,enabled)
        self.refresh();self.keep_selection(ids)
        text=f"已{'启用' if enabled else '停用'} {len(ids)-pending} 张"
        if pending and enabled:text+=f"；{pending} 张许可待确认，在“来源”里选一项后才会使用"
        self.status.setText(text)
        if enabled:self.analyze_soon()   # 重新启用的图如果还在等识别含义，接着识别
    def set_license(self):
        ids=self.selected_ids()
        if not ids:self.status.setText('先在表格里选中图片');return
        license=self.license.currentData();failed=[]
        for aid in ids:
            row=self.catalog.get(aid)
            if not row:continue
            m=row['metadata']
            try:self.catalog.review(aid,author=m.get('author',''),source=m.get('source',''),license=license)
            except ValueError as error:failed.append(str(error))
        self.refresh();self.keep_selection(ids)
        self.status.setText(f"已把 {len(ids)-len(failed)} 张设为“{LICENSE_LABELS.get(license,license)}”"
                            +(f"；{len(failed)} 张没改：{failed[0]}" if failed else ''))
        self.analyze_soon()   # 确认来源后可用的图，如果还在等识别含义，接着识别
    def save_meaning(self):
        aid=self.selected()
        if not aid:self.status.setText('选中一张图再改含义');return
        text=self.semantic.text().strip()
        if text:
            self.catalog.annotate(aid,{'semantic':text[:160]});self.status.setText('已保存含义；以后自动识别不会覆盖')
        else:
            self.queue_analysis([aid]);self.status.setText('含义已清空，会重新自动识别')
        self.refresh();self.keep_selection([aid]);self.explain_if_blocked()
    def explain_if_blocked(self):
        """开始识别；现在不能识别（额度、识图没配好等）就把原因接在状态后面，不让图一声不吭地停在“识别含义中”。"""
        if self.analyze_soon() or self.analyzer is not None:return
        reason=self.blocker()
        if reason:self.status.setText(self.status.text()+'；暂未开始：'+reason)
    def reanalyze(self):
        ids=self.selected_ids()
        if not ids:self.status.setText('先在表格里选中图片');return
        self.queue_analysis(ids);self.refresh();self.keep_selection(ids)
        self.status.setText(f'{len(ids)} 张已排队重新识别含义');self.explain_if_blocked()
    def queue_analysis(self,ids):
        """排进自动识别：清掉用户手改标记，识别结果写回含义。"""
        import json as _json
        def apply(c):
            for aid in ids:
                row=c.execute('SELECT metadata FROM expression_assets WHERE asset_id=?',(aid,)).fetchone()
                if not row:continue
                m=_json.loads(row['metadata']);m.update(analysis='pending',attempts=0);m.pop('user_annotated',None)
                c.execute('UPDATE expression_assets SET metadata=? WHERE asset_id=?',(_json.dumps(m,ensure_ascii=False),aid))
        self.catalog.db.locked(apply)
    # ---- 自动识别含义（后台，不卡界面） ---------------------------------------------------------
    def media(self):
        if self.controller is None:return None
        from aicompanion.bridge.media import QQMedia
        return QQMedia(self.catalog.db,self.controller)
    def analysis_hint(self):
        reason=self.blocker()
        if reason:return reason
        return '后台识别中' if self.analyzer is not None else '稍后自动识别'
    def blocker(self):
        """等识别含义的图现在为什么不能识别；能识别时返回空。
        实机反馈（2026-11-11）：点“重新识别含义”后一直显示“识别含义中”，原来额度用完、识图出错都没有任何提示。"""
        media=self.media()
        if media is None:return '打开设置窗口时自动识别'
        if not media.vision_ready():return '在“模型”页选好识图模型（并打开识图）后自动识别，现在先按分类名使用'
        if self.quota_stopped(self.catalog.db):return '今天的金额额度已用完，明天或在“用量”页调高额度后继续识别'
        from aicompanion.expression.collect import StickerCollector
        collector=StickerCollector(self.catalog.db,self.catalog)
        limit=collector.analysis_daily()
        if collector.analysis_count_today()>=limit:
            return f'今天已识别 {collector.analysis_count_today()} 张，到了“每天最多分析 {limit} 张”的上限；明天或调高上限后继续'
        if self.analyzer is None and not collector.pending(1):
            waiting=[r for r in self.catalog.rows() if r['metadata'].get('analysis')=='pending']
            if waiting and all(r['review_status']!='approved' or not r['enabled'] for r in waiting):
                return '等识别的图是停用或许可待确认的，启用 / 确认来源后才识别'
            if waiting and not collector.enabled() and all(r['metadata'].get('collected') for r in waiting if r['enabled']):
                return '“收集 QQ 表情包”已关闭，收集来的图暂不识别'
            if waiting:return '另一处（QQ 服务或之前打开的图库页）正在识别，稍后刷新查看'
        return ''
    @staticmethod
    def quota_stopped(database):
        """金额额度是否已到上限。识图费用记在 qq 用途上，和 QQ 后台分析用同一个检查（Codex 审计 PR #31 R2）。"""
        from aicompanion.usage.meter import Quota
        try:return Quota(database).check_silent('qq')=='stop'
        except Exception:return True   # 查不了额度时宁可不调用模型
    def analyze_soon(self):
        """后台识别几张等识别含义的图；识完一批再接下一批，直到没有、当天次数用完或金额额度到限（队列保留，下次再接着识别）。"""
        media=self.media()
        if media is None or self.analyzer is not None or not media.vision_ready():return False
        from aicompanion.expression.collect import StickerCollector
        collector=StickerCollector(self.catalog.db,self.catalog)
        if not collector.pending(1) or self.quota_stopped(self.catalog.db):return False
        if collector.analysis_count_today()>=collector.analysis_daily():return False   # 当天张数已满：不开线程，原因由 blocker 显示
        # 线程挂在主窗口上（和导入一样）：关掉设置窗口时这一批照常识别完，不会因为页面销毁而中断崩溃。
        # 同一张图不会被重开的页面或 QQ 服务再交给模型一次：collector 在分析层按素材领取（R3）。
        # 页面关掉后 worker 还在跑：每张开始前的检查不碰页面对象，只用数据库
        database,stopped=self.catalog.db,self.quota_stopped
        # Codex 复审 R5：每张开始前也复核识图是否仍可用（中途关了识图、或首张被接口拒收图片后标为不支持），
        # 不可用就结束本批、保留 pending，不预留次数也不记失败；media 是捕获的对象，不碰已关闭的页面
        def alive(token):
            return not token.event.is_set() and not stopped(database) and media.vision_ready()
        def run(token):
            collector.analyze_pending(media.analyze_sticker,limit=self.ANALYZE_BATCH,alive=lambda:alive(token))
            return collector.last
        worker=self.start_worker(run)
        self.analyzer=worker
        worker.succeeded.connect(self.analyzed);worker.failed.connect(self.analysis_failed)
        worker.finished.connect(self.analysis_finished);worker.start()
        return True
    def start_worker(self,fn):
        owner=self.window().parentWidget()
        worker=TaskWorker(fn,owner or QApplication.instance())
        if owner is not None and hasattr(owner,'tasks'):
            owner.tasks.add(worker);worker.finished.connect(lambda:owner._task_finished(worker))
        else:worker.finished.connect(worker.deleteLater)
        return worker
    def analyzed(self,last):
        """一批识别结束：说清识别成功几张、失败几张、为什么停下（失败只显示状态码 / 异常类型，不显示错误正文）。"""
        last=last if isinstance(last,dict) else {}
        self._progress=bool(last.get('done'));self.refresh()
        parts=[]
        if last.get('ok'):parts.append(f"已识别 {last['ok']} 张图的含义")
        if last.get('failed'):
            error=(self.catalog.db.get_setting('sticker_analysis_error',{}) or {}).get('reason','')
            parts.append(f"{last['failed']} 张这次没识别出来"+(f"：{error}" if error else '')+'（每张最多试 2 次，之后可点“重新识别含义”）')
        if last.get('stop'):
            reason=self.blocker()
            if reason:parts.append('已暂停：'+reason)
        if parts:self.status.setText('；'.join(parts))
    def analysis_failed(self,error):
        self.status.setText('自动识别出错：'+str(error)[:300])
    def analysis_finished(self):
        self.analyzer=None
        # 这一批识别出了东西才接着下一批；额度用完或识图失败（一张都没处理）就停，等下次刷新或打开设置
        if getattr(self,'_progress',False):self._progress=False;self.analyze_soon()
    def background(self,fn):
        # finished 会释放 QThread；只用完成信号清空的引用判断忙闲，不访问已删除的 Qt 对象。
        if self.worker is not None:self.status.setText('正在处理，请稍候');return
        worker=self.start_worker(fn);self.worker=worker
        worker.succeeded.connect(self.done);worker.failed.connect(self.failed)
        worker.finished.connect(self.background_finished)
        worker.start();self.status.setText('正在后台校验图片…')
    def background_finished(self):
        self.worker=None
    def failed(self,error):
        self.status.setText('处理失败：'+str(error)[:500]+'；原始素材文件保留')
    def done(self,value):
        self.refresh();self.analyze_soon()
        if isinstance(value,dict) and all(k in value for k in ('imported','reused','invalid')):
            self.status.setText(f"处理完成：新增 {value['imported']} 张，重复 {value['reused']} 张，失败 {value['invalid']} 张")
        else:self.status.setText('处理完成：'+str(value))
    def scan(self):
        self.background(lambda token:self.catalog.scan(token))
    def pick_import(self):
        paths,_=QFileDialog.getOpenFileNames(self,'导入表情包（可多选）','','图片 (*.png *.jpg *.jpeg *.gif *.webp)')
        if paths:
            metadata=self.metadata();self.background(lambda token:self.import_images(paths,metadata,token))
    def import_images(self,paths,metadata,token=None):
        counts=dict(imported=0,reused=0,invalid=0)
        for path in paths:
            if token is not None:token.check()
            try:
                _,reused=self.catalog.import_image(path,metadata);counts['reused' if reused else 'imported']+=1
            except (ValueError,OSError):
                if len(paths)==1:raise
                counts['invalid']+=1
        return counts
    def pick_pack(self):
        path,_=QFileDialog.getOpenFileName(self,'导入已下载的素材清单','','清单 (*.json)')
        if path:self.background(lambda token:self.catalog.import_pack(path,token))
    def clear_cache(self):
        self.catalog.clear_thumbnails();self.refresh();self.status.setText('仅清理索引内缩略图；原图和设置保留，刷新可重建')
