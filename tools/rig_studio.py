"""形象包工具：把分层源图编译成骨骼模型，并提供一个标注支点、实时预览的小窗口。规格见 docs/tasks/T6-rig-studio.md。

  python tools/rig_studio.py compile <分层包目录> output/rig
  python tools/rig_studio.py studio <分层包目录> --out output/rig
  python tools/rig_studio.py compile-poses <pose-layered-v2 目录> [输出目录] [--verify] [--texture-scale 0.5]
     十姿态分层包（原鲸娘逐姿态切片）→ AICompanionPoseRig；默认输出到 characters/deepseek-whale/rig。
     --verify 把切片按 z 回拼，和 originals/ 逐像素比对；任何一张有差异或缺原图就失败，并且不改动输出目录。

需要 Pillow（pip install -e .[studio]）。studio 里：拖动彩色圆点标注支点，拖动虚线框的两个角调整头部范围；
"保存并编译"写回 pack.json 并编译；右侧预览用的是和桌宠完全相同的渲染器，可以切姿势、表情、动作。
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))

from aicompanion.body.rig_pack import LAYERS, PACK_ID, PIVOTS, PackError, compile_pack, read_pack   # noqa: E402

POSES = ['idle', 'talking', 'thinking', 'working', 'celebrate', 'wait', 'sleepy', 'error', 'grabbed', 'poked', 'patted', 'dizzy']
EXPRESSIONS = ['happy', 'love', 'surprised', 'sleep', 'drag', 'dizzy', 'shy', 'smug']
MOTIONS = ['nod', 'shake', 'wave', 'bow', 'jump', 'spin', 'tilt', 'sway']
PIVOT_COLORS = ['#e6194b', '#3cb44b', '#4363d8', '#f58231', '#911eb4', '#42d4f4', '#f032e6', '#9a6324', '#469990', '#808000', '#000075']


def default_out(art: Path) -> Path:
    """默认输出到对应角色包的 rig/。id 只允许小写字母、数字、连字符，防止 ../ 之类让输出跑出角色目录。"""
    pack_id = str(read_pack(art).get('id', ''))
    if not PACK_ID.match(pack_id): raise PackError([f'pack.json 的 id「{pack_id}」不合法：只能用小写字母、数字和连字符'])
    return ROOT/'src'/'aicompanion'/'characters'/pack_id/'rig'


def cmd_compile(art: Path, out: Path) -> int:
    try:
        report = compile_pack(art, out)
    except PackError as error:
        print(error); return 1
    print(f'已编译到 {out}：{report.parts} 个部件，表情 {"、".join(report.expressions)}')
    for warning in report.warnings: print('提示：', warning)
    return 0


def cmd_compile_poses(art: Path, out: Path | None, verify: bool, texture_scale: float) -> int:
    from aicompanion.body.pose_pack import compile_posepack, read_source
    try:
        if out is None:
            character = str(read_source(art).get('id') or 'deepseek-whale')
            if not PACK_ID.match(character): raise PackError([f'model.json 的 id「{character}」不合法'])
            out = ROOT/'src'/'aicompanion'/'characters'/character/'rig'
        report = compile_posepack(art, out, texture_scale=texture_scale, verify=verify)
    except PackError as error:
        print(error); return 1
    print(f'已编译到 {out}：{len(report.poses)} 个姿态，{report.parts} 个图层，{report.variants} 个替换层')
    if verify: print(f'回拼比对：{len(report.verified)} 个状态全部零差异（Alpha 整幅、颜色按覆盖像素）')
    if report.disabled:
        print(f'局部摆动未就绪（没有标注支点，幅度为 0）：{len(report.disabled)} 处，如 {"、".join(report.disabled[:6])}')
    for warning in report.warnings: print('提示：', warning)
    return 0


def composite(art: Path, pack: dict):
    """按 z 叠出整张角色图（含中性五官），给标注窗口当底图。"""
    from PIL import Image
    canvas = tuple(pack.get('canvas') or (2048, 2048))
    image = Image.new('RGBA', canvas, (0, 0, 0, 0))
    layers = sorted((spec['z'], name) for name, spec in LAYERS.items() if spec.get('group') != 'sit')
    face = art/'layers'/'face'/'neutral'
    features = [face/f'{side}_{part}.png' for side in ('eyeL', 'eyeR') for part in ('ball', 'iris', 'hl', 'lash')] + [face/'mouth.png', face/'brows.png']
    for z, name in layers:
        path = art/'layers'/f'{name}.png'
        if path.is_file():
            with Image.open(path) as layer:
                if layer.size == canvas: image.alpha_composite(layer.convert('RGBA'))
        if z == 8.0:   # 脸之后叠五官
            for path in features:
                if path.is_file():
                    with Image.open(path) as layer:
                        if layer.size == canvas: image.alpha_composite(layer.convert('RGBA'))
    return image


def cmd_studio(art: Path, out: Path) -> int:
    from aicompanion.body.webengine_flags import apply_webengine_flags
    apply_webengine_flags()
    from PySide6.QtCore import QPointF, QRectF, Qt
    from PySide6.QtGui import QBrush, QColor, QImage, QPen, QPixmap
    from PySide6.QtWidgets import (QApplication, QComboBox, QGraphicsEllipseItem, QGraphicsRectItem, QGraphicsScene,
                                   QGraphicsSimpleTextItem, QGraphicsView, QHBoxLayout, QLabel, QMainWindow, QPlainTextEdit,
                                   QPushButton, QVBoxLayout, QWidget, QGraphicsItem)
    app = QApplication(sys.argv)

    class Handle(QGraphicsEllipseItem):
        def __init__(self, name, color, point, radius, on_move):
            super().__init__(-radius, -radius, radius*2, radius*2)
            self.name = name; self.on_move = on_move
            self.setBrush(QBrush(QColor(color))); self.setPen(QPen(QColor('white'), radius*0.25))
            self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemSendsGeometryChanges)
            self.setPos(QPointF(*point)); self.setZValue(10); self.setToolTip(name)
            label = QGraphicsSimpleTextItem(name, self); label.setBrush(QBrush(QColor(color)))
            font = label.font(); font.setPointSizeF(radius*1.6); label.setFont(font); label.setPos(radius*1.2, -radius*1.2)

        def itemChange(self, change, value):
            if change == QGraphicsItem.GraphicsItemChange.ItemPositionHasChanged: self.on_move(self)
            return super().itemChange(change, value)

    class Studio(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(f'形象包标注 · {art}')
            self.pack = read_pack(art); self.preview = None
            canvas = self.pack.get('canvas') or [2048, 2048]; radius = canvas[1]/120
            self.scene = QGraphicsScene(self); self.view = QGraphicsView(self.scene)
            self.view.setRenderHints(self.view.renderHints()); self.view.setBackgroundBrush(QColor('#eef2ff'))
            self.base = self.scene.addPixmap(QPixmap()); self._reload_image()
            ground = canvas[1]*0.96875 if not self.pack.get('origin') else self.pack['origin'].get('ground_y', canvas[1]*0.96875)
            self.scene.addLine(0, ground, canvas[0], ground, QPen(QColor('#9aa7c7'), radius*0.2, Qt.PenStyle.DashLine))
            pivots = self.pack.get('pivots') or {}
            self.handles = {}
            for i, name in enumerate(PIVOTS):
                point = pivots.get(name) or [canvas[0]/2 + (i-5)*radius*3, canvas[1]*0.5]
                self.handles[name] = Handle(name, PIVOT_COLORS[i % len(PIVOT_COLORS)], point, radius, self._moved)
                self.scene.addItem(self.handles[name])
            head = self.pack.get('head') or [canvas[0]*0.28, canvas[1]*0.05, canvas[0]*0.76, canvas[1]*0.62]
            self.head = QGraphicsRectItem(); self.head.setPen(QPen(QColor('#4D6BFE'), radius*0.25, Qt.PenStyle.DashLine))
            self.scene.addItem(self.head)
            self.corners = [Handle('head↖', '#4D6BFE', head[:2], radius*0.8, self._moved), Handle('head↘', '#4D6BFE', head[2:], radius*0.8, self._moved)]
            for c in self.corners: self.scene.addItem(c)
            self._moved(None)

            side = QVBoxLayout()
            self.info = QLabel(''); self.info.setWordWrap(True); side.addWidget(self.info)
            save = QPushButton('保存并编译'); save.clicked.connect(self.save); side.addWidget(save)
            reload = QPushButton('重新载入图层'); reload.clicked.connect(self._reload_image); side.addWidget(reload)
            self.pose = QComboBox(); self.pose.addItems(POSES); self.pose.currentTextChanged.connect(lambda p: self.preview and self.preview.set_pose(p))
            side.addWidget(QLabel('预览姿势')); side.addWidget(self.pose)
            row = QHBoxLayout()
            for name in EXPRESSIONS:
                button = QPushButton(name); button.clicked.connect(lambda _=False, n=name: self.preview and self.preview.perform('expression', n)); row.addWidget(button)
            side.addWidget(QLabel('表情')); side.addLayout(row)
            row = QHBoxLayout()
            for name in MOTIONS:
                button = QPushButton(name); button.clicked.connect(lambda _=False, n=name: self.preview and self.preview.perform('motion', n)); row.addWidget(button)
            side.addWidget(QLabel('动作')); side.addLayout(row)
            self.preview_box = QVBoxLayout(); side.addLayout(self.preview_box, 1)
            self.log = QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumHeight(160); side.addWidget(self.log)
            panel = QWidget(); panel.setLayout(side); panel.setFixedWidth(460)
            body = QWidget(); layout = QHBoxLayout(body); layout.addWidget(self.view, 1); layout.addWidget(panel)
            self.setCentralWidget(body); self.resize(1400, 900)
            self.view.fitInView(QRectF(0, 0, *canvas), Qt.AspectRatioMode.KeepAspectRatio)
            if (out/'rig.json').is_file(): self._show_preview()

        def _reload_image(self):
            image = composite(art, self.pack).convert('RGBA')
            data = image.tobytes('raw', 'RGBA')
            self.base.setPixmap(QPixmap.fromImage(QImage(data, image.width, image.height, QImage.Format.Format_RGBA8888).copy()))

        def _moved(self, handle):
            if not hasattr(self, 'corners'): return
            a, b = self.corners[0].pos(), self.corners[1].pos()
            self.head.setRect(QRectF(a, b).normalized())
            if hasattr(self, 'info') and handle is not None:
                self.info.setText(f'{handle.name}: ({handle.pos().x():.0f}, {handle.pos().y():.0f})')

        def save(self):
            self.pack['pivots'] = {name: [round(h.pos().x(), 1), round(h.pos().y(), 1)] for name, h in self.handles.items()}
            r = self.head.rect(); self.pack['head'] = [round(r.left(), 1), round(r.top(), 1), round(r.right(), 1), round(r.bottom(), 1)]
            (art/'pack.json').write_text(json.dumps(self.pack, ensure_ascii=False, indent=1), encoding='utf-8')
            try:
                report = compile_pack(art, out)
            except PackError as error:
                self.log.setPlainText(str(error)); return
            self.log.setPlainText(f'已保存并编译：{report.parts} 个部件，表情 {"、".join(report.expressions)}\n' + '\n'.join(report.warnings))
            self._show_preview()

        def _show_preview(self):
            from aicompanion.body.rig_view import RigAvatar
            if self.preview is not None: self.preview.shutdown(); self.preview.deleteLater()
            self.preview = RigAvatar(out/'rig.json'); self.preview.setMinimumSize(360, 360)
            self.preview.set_surface(False)
            self.preview_box.addWidget(self.preview)
            self.preview.set_pose(self.pose.currentText())

    window = Studio(); window.show()
    return app.exec()


def main():
    parser = argparse.ArgumentParser(description='形象包：编译与标注')
    sub = parser.add_subparsers(dest='command', required=True)
    c = sub.add_parser('compile'); c.add_argument('art'); c.add_argument('out', nargs='?')
    s = sub.add_parser('studio'); s.add_argument('art'); s.add_argument('--out')
    p = sub.add_parser('compile-poses'); p.add_argument('art'); p.add_argument('out', nargs='?')
    p.add_argument('--verify', action='store_true'); p.add_argument('--texture-scale', type=float, default=0.5)
    args = parser.parse_args()
    art = Path(args.art).resolve()
    if args.command == 'compile-poses':
        if not 0.1 <= args.texture_scale <= 1: print('--texture-scale 应在 0.1 到 1 之间'); return 1
        return cmd_compile_poses(art, Path(args.out).resolve() if args.out else None, args.verify, args.texture_scale)
    try: out = Path(args.out).resolve() if args.out else default_out(art)
    except PackError as error: print(error); return 1
    return cmd_compile(art, out) if args.command == 'compile' else cmd_studio(art, out)


if __name__ == '__main__':
    raise SystemExit(main())
