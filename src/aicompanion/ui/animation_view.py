"""透明 WebP 图集播放器：后台解码、单动作缓存，保留原生桌宠交互。"""
from __future__ import annotations
import math
import time
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QImage, QImageReader, QPainter, QTransform
from PySide6.QtWidgets import QWidget
from aicompanion.characters import CharacterPackError
from aicompanion.characters.animation_pack import load_animation_pack

# 一张 120 帧图集约 59.3 MiB，切换后释放旧图集；不会解码整套动作常驻内存。
FRAME_HEIGHT = 270
FADE = .16


class Decoder(QObject):
    decoded = Signal(object, QImage, str)

    @Slot(object)
    def decode(self, action):
        try:
            reader=QImageReader(str(action.path),b'webp')
            expected=QSize(action.width*action.columns,action.height*action.rows)
            if reader.size()!=expected:
                raise ValueError('图集尺寸与清单不一致。')
            height=min(FRAME_HEIGHT,action.height)
            width=round(action.width*height/action.height)
            target=QSize(width*action.columns,height*action.rows)
            reader.setScaledSize(target)
            image=reader.read()
            if image.isNull() or not image.hasAlphaChannel():
                raise ValueError('图集无法解码或没有透明通道。')
            if image.size()!=target: image=image.scaled(target,Qt.AspectRatioMode.IgnoreAspectRatio,Qt.TransformationMode.SmoothTransformation)
            image=image.convertToFormat(QImage.Format.Format_ARGB32_Premultiplied)
            self.decoded.emit(action,image,'')
        except (OSError,ValueError,RuntimeError) as error:
            self.decoded.emit(action,QImage(),str(error))


class AnimationAvatar(QWidget):
    poseShown = Signal(str)
    landed = Signal()
    frameAdvanced = Signal(int)
    failed = Signal(str)
    decodeRequested = Signal(object)

    def __init__(self, manifest_path, parent=None):
        super().__init__(parent)
        self.pack=load_animation_pack(manifest_path); self.native_size=self.pack.size
        self.poster=QImage(str(self.pack.poster))
        if self.poster.isNull() or self.poster.size()!=QSize(*self.native_size):
            raise CharacterPackError('动画封面损坏或尺寸不符。')
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self.pose='idle'; self.action='idle'; self.mood='calm'; self.mouth=0.; self.voice_active=False
        self.dragging=False; self.walking=0; self.direction=-1; self._cue=None; self._cue_until=0.
        self._sheet=QImage(); self._frame=self.poster; self._previous=QImage()
        self._loaded=None; self._requested=None; self._loading=None; self._index=-1
        self._started=time.monotonic(); self._fade_started=0.; self._closed=False
        self._hop_started=-1.; self._hop_duration=0.; self._hop_height=0.
        self._bounce_started=-1.; self._bounce_strength=0.
        self.thread=QThread(self); self.decoder=Decoder(); self.decoder.moveToThread(self.thread)
        self.decodeRequested.connect(self.decoder.decode)
        self.decoder.decoded.connect(self._decoded)
        self.thread.finished.connect(self.decoder.deleteLater); self.thread.start()
        self.timer=QTimer(self); self.timer.setInterval(16); self.timer.timeout.connect(self.advance)

    def set_state(self,mood='calm',mouth=0):
        self.mood=mood or 'calm'; self.mouth=max(0.,min(1.,float(mouth)))

    def set_voice_active(self,active): self.voice_active=bool(active)
    def set_surface(self,desktop): pass

    def set_pose(self,pose):
        if pose==self.pose: return
        self.pose=pose if pose in self.pack.poses else 'idle'
        self.poseShown.emit(self.pose); self._select()

    def _select(self):
        now=time.monotonic()
        if self.pose in ('grabbed','falling') or self.dragging:
            key=self.pack.poses.get('grabbed','idle')
        elif self.pose=='idle' and self.walking:
            key=self.pack.poses.get('walking','idle')
        elif self._cue is not None and now<self._cue_until and self.pose not in ('error','wait','dizzy'):
            key=self._cue
        else:
            key=self.pack.poses.get(self.pose,self.pack.poses['idle'])
        self.action=key; self._requested=key
        if self._loaded==key or self._loading is not None or not self.isVisible() or self._closed: return
        self._loading=key; self.decodeRequested.emit(self.pack.actions[key])

    @Slot(object,QImage,str)
    def _decoded(self,action,image,error):
        if self._loading==action.id: self._loading=None
        if self._closed or not self.isVisible(): return
        if action.id!=self._requested:
            self._select(); return
        if error:
            self.failed.emit(error); return
        self._previous=self._frame.copy(); self._sheet=image; self._loaded=action.id
        self._started=time.monotonic(); self._fade_started=self._started; self._index=-1
        self.advance()

    def advance(self):
        if self._closed: return
        now=time.monotonic()
        if self._cue is not None and now>=self._cue_until:
            self._cue=None; self._select()
        changed=False
        if self._loaded is not None:
            action=self.pack.actions[self._loaded]; index=action.frame_at(now-self._started)
            if index!=self._index:
                self._index=index
                w=self._sheet.width()//action.columns; h=self._sheet.height()//action.rows
                self._frame=self._sheet.copy(index%action.columns*w,index//action.columns*h,w,h)
                self.frameAdvanced.emit(index); changed=True
        if self._hop_started>=0 and now-self._hop_started>=self._hop_duration:
            self._hop_started=-1.; self.landed.emit(); changed=True
        fading=not self._previous.isNull()
        if fading and now-self._fade_started>=FADE:
            self._previous=QImage()
        if changed or fading or self.hopping or now-self._bounce_started<.4: self.update()

    def content_rect(self):
        scale=min(self.width()/self.native_size[0],self.height()/self.native_size[1])
        w,h=(v*scale for v in self.native_size)
        return QRectF((self.width()-w)/2,self.height()-h,w,h)

    def _transform(self):
        rect=self.content_rect(); now=time.monotonic(); t=QTransform()
        t.translate(rect.center().x(),rect.bottom())
        if self.hopping:
            age=(now-self._hop_started)/self._hop_duration
            t.translate(0,-math.sin(age*math.pi)*self._hop_height*rect.height())
        age=now-self._bounce_started
        squash=math.sin(age*25)*math.exp(-age*10)*.045*self._bounce_strength if 0<=age<.4 else 0.
        facing=self.pack.actions[self._loaded or self.pack.poses['idle']].facing
        mirror=self.direction!=(1 if facing=='right' else -1)
        t.scale((-1 if mirror else 1)*(1+squash),1-squash)
        t.translate(-rect.center().x(),-rect.bottom())
        return t

    def paintEvent(self,event):
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(),Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setTransform(self._transform()); rect=self.content_rect()
        if not self._previous.isNull():
            opacity=min(1.,max(0.,(time.monotonic()-self._fade_started)/FADE))
            painter.setOpacity(1-opacity); painter.drawImage(rect,self._previous)
            painter.setOpacity(opacity)
        painter.drawImage(rect,self._frame); painter.end()

    def _image_point(self,point):
        inverse,ok=self._transform().inverted()
        if not ok: return None
        mapped=inverse.map(QPointF(point)); rect=self.content_rect()
        if not rect.contains(mapped) or rect.isEmpty(): return None
        return ((mapped.x()-rect.x())/rect.width(),(mapped.y()-rect.y())/rect.height())

    def hit(self,point):
        located=self._image_point(point)
        if located is None: return False
        x,y=located
        px=min(self._frame.width()-1,int(x*self._frame.width()))
        py=min(self._frame.height()-1,int(y*self._frame.height()))
        return px>=0 and py>=0 and self._frame.pixelColor(px,py).alpha()>40

    @property
    def cursor_hit(self): return self.hit(self.mapFromGlobal(QCursor.pos()))

    def head_region(self,point):
        located=self._image_point(point)
        if located is None or not self.hit(point): return False
        x,y=located; hx,hy,hw,hh=self.pack.head
        return hx<=x<=hx+hw and hy<=y<=hy+hh

    def head_point(self):
        rect=self.content_rect(); hx,hy,hw,hh=self.pack.head
        return QPointF(rect.x()+(hx+hw/2)*rect.width(),rect.y()+hy*rect.height()).toPoint()

    def face(self,direction):
        self.direction=1 if direction>=0 else -1; self.update()

    def set_drag(self,active,velocity_x=0.):
        self.dragging=bool(active); self._select()

    def set_walking(self,direction):
        self.walking=int(direction)
        if direction: self.face(direction)
        self._select()

    @property
    def hopping(self): return self._hop_started>=0 and 0<=time.monotonic()-self._hop_started<self._hop_duration

    def hop(self,height=.06,duration=.42):
        if self.hopping: return
        self._hop_started=time.monotonic(); self._hop_duration=max(.1,duration); self._hop_height=height

    def bounce(self,strength=1.):
        self._bounce_started=time.monotonic(); self._bounce_strength=strength

    def perform(self,kind,name):
        if self.pose in ('grabbed','falling'): return
        if kind=='motion' and name=='jump': self.hop(); return
        mapping={'wave':'wave','nod':'patted','shake':'poked','happy':'patted','love':'patted',
                 'smug':'patted','shy':'patted','surprised':'poked','sleep':'sleepy','dizzy':'error','drag':'grabbed'}
        pose=mapping.get(name)
        if pose in self.pack.poses:
            self._cue=self.pack.poses[pose]; self._cue_until=time.monotonic()+2.2; self._select()
        elif kind=='motion': self.bounce(.8)

    def play_action(self,key):
        if key not in self.pack.actions or self.pose in ('grabbed','falling'): return
        self._cue=key; action=self.pack.actions[key]
        self._cue_until=time.monotonic()+action.count/action.fps; self._select()

    def showEvent(self,event):
        super().showEvent(event)
        if not self._closed: self.timer.start(); self._select()

    def hideEvent(self,event):
        self.timer.stop(); self._sheet=QImage(); self._loaded=None; self._previous=QImage()
        super().hideEvent(event)

    def shutdown(self):
        if self._closed: return
        self._closed=True; self.timer.stop(); self.thread.quit(); self.thread.wait()
        self._sheet=QImage(); self._previous=QImage()
