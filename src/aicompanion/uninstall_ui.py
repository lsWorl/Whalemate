"""卸载窗口在临时副本中运行，原进程退出后才能删除原 exe 并报告最终结果。"""
import argparse
import base64
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

from PySide6.QtCore import QThread, Signal
from PySide6.QtWidgets import (QApplication, QCheckBox, QDialog, QHBoxLayout, QLabel, QMessageBox,
                               QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout)
from aicompanion.ui import theme
from aicompanion.ui.icons import app_icon
from aicompanion.uninstall import EXE, Locations, execute, scan


class Work(QThread):
    progress = Signal(int, int, str)
    result = Signal(object)
    error = Signal(str)

    def __init__(self, operation, parent):
        super().__init__(parent)
        self.operation = operation

    def run(self):
        try:
            self.result.emit(self.operation(self))
        except Exception as error:
            self.error.emit(str(error))


class UninstallWindow(QDialog):
    def __init__(self, locations, preview=False):
        super().__init__()
        self.locations = locations
        self.preview = preview
        self.worker = None
        self.plan = None
        self.setWindowTitle('卸载鲸伴')
        self.resize(860, 590)
        self.setStyleSheet(theme.COMMON)
        body = QVBoxLayout(self)
        self.status = QLabel('正在扫描鲸伴程序和存储位置…')
        self.status.setObjectName('CardTitle')
        body.addWidget(self.status)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        body.addWidget(self.details)
        self.gallery = QCheckBox('同时删除我的表情图库（里面可能有你自己收集的图片）')
        self.gallery.hide()
        self.gallery.toggled.connect(self.refresh)
        body.addWidget(self.gallery)
        self.confirm = QCheckBox('我知道删除后无法恢复')
        self.confirm.setVisible(not preview)
        body.addWidget(self.confirm)
        self.bar = QProgressBar()
        self.bar.setRange(0, 0)
        body.addWidget(self.bar)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.close_button = QPushButton('关闭')
        self.close_button.clicked.connect(self.reject)
        buttons.addWidget(self.close_button)
        self.remove = QPushButton('卸载')
        self.remove.setObjectName('Primary')
        self.remove.setEnabled(False)
        self.remove.setVisible(not preview)
        self.confirm.toggled.connect(lambda on: self.remove.setEnabled(bool(self.plan) and on))
        self.remove.clicked.connect(self.start_removal)
        buttons.addWidget(self.remove)
        body.addLayout(buttons)
        theme.prepare_controls(self)
        self.run_job(lambda _: scan(locations), self.loaded)

    def run_job(self, operation, callback):
        self.worker = Work(operation, self)
        self.worker.progress.connect(self.progress)
        self.worker.result.connect(callback)
        self.worker.error.connect(self.error)
        self.worker.start()

    def reject(self):
        if not self.worker or not self.worker.isRunning():
            super().reject()

    def loaded(self, plan):
        self.plan = plan
        self.bar.hide()
        self.gallery.setVisible(bool(plan.galleries))
        self.refresh()

    def refresh(self):
        if self.plan:
            self.confirm.setChecked(False)
            self.details.setPlainText('\n'.join(self.plan.lines(self.gallery.isChecked())))
            self.status.setText('请核对每一项的路径、大小和用途。' if not self.preview else '卸载预览')

    def progress(self, done, total, path):
        self.bar.setRange(0, total)
        self.bar.setValue(done)
        self.status.setText('正在删除 '+path)

    def start_removal(self):
        if self.preview or not self.confirm.isChecked() or self.worker.isRunning():
            return
        confirmed, galleries = self.confirm.isChecked(), self.gallery.isChecked()
        self.confirm.setEnabled(False)
        self.gallery.setEnabled(False)
        self.remove.setEnabled(False)
        self.bar.show()
        self.run_job(lambda job: execute(self.plan, confirmed=confirmed, galleries=galleries,
                                         progress=lambda i, n, path: job.progress.emit(i, n, str(path))), self.complete)

    def error(self, message):
        self.bar.hide()
        self.status.setText(message)
        if self.plan:
            self.confirm.setEnabled(True)
            self.confirm.setChecked(False)
            self.gallery.setEnabled(True)

    def complete(self, result):
        ok, failed = result
        self.status.setText(f'卸载结束。成功 {len(ok)} 项，失败 {len(failed)} 项。')
        lines = ['成功\t'+path for path in ok]
        if failed:
            lines += ['关闭占用程序后，按以下路径手动删除；权限不足时请检查文件权限。']
            lines.extend('失败\t'+error for error in failed)
        self.details.setPlainText('\n'.join(lines))
        self.bar.hide()
        self.confirm.hide()
        self.gallery.hide()
        self.remove.hide()


def output_preview(message):
    if sys.stdout is not None:
        if sys.stdout.isatty():
            print(message, flush=True)
        else:
            sys.stdout.buffer.write((message+'\n').encode('utf-8'))
            sys.stdout.buffer.flush()
        return True
    # PyInstaller windowed 的 sys.stdout 为 None，但调用方仍可重定向 Win32 标准句柄。
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL('kernel32', use_last_error=True)
    kernel.GetStdHandle.restype = wintypes.HANDLE
    handle = kernel.GetStdHandle(-11)
    if handle in (None, wintypes.HANDLE(-1).value):
        return False
    data = (message+'\n').encode('utf-8')
    count = wintypes.DWORD()
    return bool(kernel.WriteFile(wintypes.HANDLE(handle), data, len(data), ctypes.byref(count), None))


def copy_launcher(executable):
    directory = Path(tempfile.mkdtemp(prefix='whalemate-uninstall-'))
    copy = directory/EXE
    try:
        shutil.copyfile(executable, copy)
        subprocess.Popen([str(copy), '--origin', str(executable.parent)], cwd=directory,
                         env=dict(os.environ, PYINSTALLER_RESET_ENVIRONMENT='1'), close_fds=True)
    except BaseException:
        copy.unlink(missing_ok=True)
        directory.rmdir()
        raise


def clean_temporary_launcher(executable):
    # 动态路径只作环境数据传入；只尝试删除自己的临时文件及已空目录。
    script = r"""
$p = $env:WHALEMATE_TEMP_LAUNCHER
$d = [System.IO.Path]::GetDirectoryName($p)
if ([System.IO.Path]::GetDirectoryName($d) -ne [System.IO.Path]::GetTempPath().TrimEnd('\') -or
    [System.IO.Path]::GetFileName($d) -notlike 'whalemate-uninstall-*' -or
    [System.IO.Path]::GetFileName($p) -ne '卸载鲸伴.exe') { exit 1 }
for ($i = 0; $i -lt 120; $i++) {
    try { [IO.File]::Delete($p); [IO.Directory]::Delete($d); exit 0 }
    catch { Start-Sleep -Milliseconds 500 }
}
"""
    encoded = base64.b64encode(script.encode('utf-16-le')).decode('ascii')
    shell = Path(os.environ['SystemRoot'])/'System32/WindowsPowerShell/v1.0/powershell.exe'
    subprocess.Popen([str(shell), '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
                     env=dict(os.environ, WHALEMATE_TEMP_LAUNCHER=str(executable)),
                     cwd=tempfile.gettempdir(), creationflags=subprocess.CREATE_NO_WINDOW, close_fds=True)


def main():
    parser = argparse.ArgumentParser(description='预览并卸载鲸伴')
    parser.add_argument('--dry-run', action='store_true', help='只输出清单')
    parser.add_argument('--origin', type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    frozen = bool(getattr(sys, 'frozen', False))
    executable = Path(sys.executable)
    install = executable.parent if frozen else Path.cwd()
    temp_copy = (frozen and executable.name == EXE and executable.parent.name.startswith('whalemate-uninstall-')
                 and executable.parent.parent == Path(tempfile.gettempdir()).resolve())
    app = QApplication.instance() or QApplication([])
    app.setWindowIcon(app_icon())
    try:
        if args.origin:
            if not temp_copy:
                raise ValueError('只有卸载程序的临时副本可接管已启动的卸载窗口')
            install = args.origin
        elif frozen and not args.dry_run:
            copy_launcher(executable)
            return 0
        locations = Locations.current(install)
        if args.dry_run:
            plan = scan(locations)
            if output_preview('\n'.join(plan.lines())):
                return 0
        window = UninstallWindow(locations, preview=args.dry_run)
        window.exec()
        return 0
    except Exception as error:
        if not args.dry_run or not output_preview('卸载失败：'+str(error)):
            QMessageBox.critical(None, '卸载鲸伴', str(error))
        return 1
    finally:
        if temp_copy:
            clean_temporary_launcher(executable)
