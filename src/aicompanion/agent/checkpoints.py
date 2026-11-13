"""跨天办事的公开进度与产物凭据。模型只能声明计划、材料和交付清单，检查点由工具完成事件写入。"""
import hashlib
import json
import os
import re
from pathlib import Path
import stat
import threading

from aicompanion.agent.attachments import checked_path
from aicompanion.agent.tools_server import _secret_reason


def public_note(text):
    return re.sub(r'<think\b[^>]*>.*?(?:</think\s*>|$)', '', text, flags=re.I | re.S).strip()


def current_artifacts(rows):
    """移动/删除的旧路径退出当前清单，原检查点仍保留供回查。"""
    files = {}
    for row in rows:
        for removed in json.loads(row['removed']):
            old = Path(removed)
            files = {p: stamp for p, stamp in files.items() if not p.is_relative_to(old)}
        for item in json.loads(row['artifacts']):
            files[Path(item['path'])] = item
    return files


def snapshot(value, secrets, *, check_format=False):
    path = checked_path(value)
    reason = _secret_reason(path, secrets)
    if reason or '.git' in (p.casefold() for p in path.parts):
        raise ValueError(f'受保护的产物路径：{path}')
    info = path.lstat()
    if stat.S_ISDIR(info.st_mode):
        return {'path': str(path), 'kind': 'directory'}
    if not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
        raise ValueError(f'产物不是独立的普通文件：{path}')
    with path.open('rb') as stream:
        if not os.path.samestat(info, os.fstat(stream.fileno())):
            raise ValueError(f'产物已被替换：{path}')
        digest = hashlib.file_digest(stream, 'sha256').hexdigest()
        if check_format:
            stream.seek(0)
            _format(path, stream, info.st_size)
        end = checked_path(path).lstat()
        if not os.path.samestat(info, end) or (info.st_size, info.st_mtime_ns) != (end.st_size, end.st_mtime_ns):
            raise ValueError(f'核对期间文件发生变化：{path}')
    return {'path': str(path), 'kind': 'file', 'size': info.st_size, 'sha256': digest}


def _format(path, stream, size):
    from aicompanion.agent.attachment_text import document_text
    suffix = path.suffix.lower()
    if suffix not in ('.json', '.docx', '.xlsx', '.pdf', '.png', '.jpg', '.jpeg', '.webp'):
        return
    if size > 32 * 1024 * 1024:
        raise ValueError(f'{path.name} 超出单文件格式核对上限（32 MiB）')
    data = stream.read(size + 1)
    if suffix == '.json':
        json.loads(data)
    elif suffix in ('.docx', '.xlsx'):
        document_text(path, data)
    elif suffix == '.pdf':
        from PySide6.QtCore import QBuffer, QIODevice
        from PySide6.QtPdf import QPdfDocument
        device = QBuffer()
        device.setData(data)
        device.open(QIODevice.OpenModeFlag.ReadOnly)
        doc = QPdfDocument(None)
        doc.load(device)
        if not doc.pageCount():
            raise ValueError('PDF 无法打开或没有页面')
    else:
        from PySide6.QtGui import QImage
        if QImage.fromData(data, suffix[1:].encode()).isNull():
            raise ValueError('不能按声明的图片格式打开')


def history(db, run_id):
    run = db.agent_run(run_id)
    rows = db.agent_checkpoints(run_id)
    lines = ['原计划：' + ' → '.join(json.loads(run['plan'])), '暂停原因：' + run['pause_reason']]
    for row in rows[-20:]:
        lines.append(f"{row['created_at']} {row['summary']}")
    for stamp in list(current_artifacts(rows).values())[-30:]:
        lines.append(f"产物 {stamp['path']} {stamp.get('sha256', '目录')}")
    # 旧版只有工具审计和执行前指纹；在写完审计之前崩溃的操作也必须标为结果待核实。
    effects = db.get_setting(f'agent_effects:{run_id}', [])
    if effects:
        lines.append(f'已有 {len(effects)} 个操作在执行前留了指纹。已尝试的操作不得重放，结果不明先查现状。')
        with db.transaction() as c:
            notes = c.execute('SELECT signature,description FROM agent_effect_notes WHERE run_id=? ORDER BY rowid', (run_id,)).fetchall()
        for note in notes[-50:]:
            lines.append(f"已预留 {note['signature'][:12]} {note['description']}（结果以检查点为准）")
        old_steps = db.agent_steps(run_id)[-30:] if len(notes) < len(effects) else []
        for step in old_steps:
            if step['tool'] in ('write_file', 'edit_file', 'move_path', 'make_dir', 'delete_path', 'run_powershell'):
                try:
                    args = json.loads(step['args_json'])
                except ValueError:
                    args = {}
                target = args.get('path') or args.get('source') or args.get('reason') or ''
                lines.append(f"已尝试 {step['tool']} {str(target)[:160]}，{step['error'][:80] or '见检查点与操作日志'}")
    return '\n'.join(lines)


class Journey:
    def __init__(self, db, run_id, folders, secrets, pause, originals=()):
        self.db, self.run_id = db, run_id
        self.folders, self.secrets, self.pause = folders, secrets, pause
        self.originals = set(originals)
        self.lock = threading.RLock()
        self.delivered = False
        self.delivery_error = ''
        self.active = 0
        self.sealed = False

    def enter(self):
        with self.lock:
            if self.sealed:
                raise ValueError('本轮已经结束')
            self.active += 1
            self.delivered = False

    def leave(self):
        with self.lock:
            self.active -= 1

    def checkpoint(self, summary, artifacts=(), removed=()):
        with self.db.transaction() as c:
            c.execute('INSERT INTO agent_checkpoints(run_id,summary,artifacts,removed) VALUES (?,?,?,?)',
                      (self.run_id, public_note(summary)[:500], json.dumps(artifacts, ensure_ascii=False), json.dumps(removed, ensure_ascii=False)))

    def plan(self, steps):
        if not isinstance(steps, list) or len(steps) > 80 or not all(isinstance(s, str) and s.strip() for s in steps):
            raise ValueError('计划需要明确的步骤，每次最多 80 步')
        with self.db.transaction() as c:
            c.execute("UPDATE agent_runs SET plan=? WHERE id=? AND status='running'",
                      (json.dumps([public_note(s)[:300] for s in steps], ensure_ascii=False), self.run_id))

    def declared_path(self, value):
        path = checked_path(value)
        if path in self.originals or _secret_reason(path, self.secrets) or '.git' in (p.casefold() for p in path.parts):
            raise ValueError(f'无法用这个路径登记产物：{path}')
        if not any(path.is_relative_to(folder) for folder in self.folders):
            raise ValueError(f'声明的路径不在工作区：{path}')
        return path

    def completed(self, record):
        with self.lock:
            outputs = [snapshot(path, self.secrets) for path in record.get('outputs', [])]
            if path := record.get('output'):
                outputs.append(snapshot(path, self.secrets))
            removed = [str(record['removed'])] if record.get('removed') else []
            if removed and outputs and outputs[0]['kind'] == 'directory':
                old = Path(removed[0])
                for previous in current_artifacts(self.db.agent_checkpoints(self.run_id)):
                    if previous != old and previous.is_relative_to(old):
                        outputs.append(snapshot(Path(path) / previous.relative_to(old), self.secrets))
            args = record['args']
            target = str(record.get('output') or args.get('path') or args.get('reason') or args.get('pattern') or '')
            self.checkpoint(record['tool'] + ' ' + target[:300], outputs, removed)

    def stage(self, action, summary, steps, files):
        summary = public_note(summary) if isinstance(summary, str) else ''
        if not summary:
            raise ValueError('请填写用户可见的阶段结果或所缺材料')
        with self.lock:
            if self.active > 1:
                raise ValueError('请等本阶段其他工具结束后再登记')
            if action == 'plan':
                if not steps:
                    raise ValueError('请列出计划步骤')
                self.plan(steps)
            elif action in ('checkpoint', 'deliver'):
                if not isinstance(files, list) or len(files) > 100:
                    raise ValueError('请逐项列出产物路径（最多 100 项）')
                existing = current_artifacts(self.db.agent_checkpoints(self.run_id))
                stamps = []
                for value in files:
                    path = checked_path(value)
                    if path in existing:
                        stamps.append(existing[path])  # 不重新盖章；最终必须与工具写入时的哈希一致。
                        continue
                    path = self.declared_path(value)
                    if action == 'deliver':
                        raise ValueError(f'交付物缺少阶段检查点：{path}。请先核实并登记 checkpoint。')
                    stamps.append(snapshot(path, self.secrets))
                self.checkpoint(summary, stamps)
                self.delivered = action == 'deliver'
                return '交付清单已登记，结束前由程序重新核对。' if self.delivered else '阶段产物已登记。'
            elif action == 'wait':
                self.checkpoint('等待材料：' + summary)
                self.pause('等待材料：' + summary[:300])
                return '已暂停。'
            else:
                raise ValueError('未知阶段操作')
            self.checkpoint(summary)
            return '计划已登记。'

    def verify(self):
        with self.lock:
            self.sealed = True
            if self.active or not self.delivered:
                raise ValueError(self.delivery_error or '尚未登记最终交付清单，或仍有工具在运行')
            for path, expected in current_artifacts(self.db.agent_checkpoints(self.run_id)).items():
                try:
                    if snapshot(path, self.secrets, check_format=True) != expected:
                        raise ValueError('大小、类型或 SHA-256 与检查点不符')
                except Exception as error:
                    raise ValueError(f'{path}：{error}') from error
