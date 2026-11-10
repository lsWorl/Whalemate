"""透明 WebP 图集播放器：后台解码、有限预载和可打断过渡，保留原生桌宠交互。"""
from __future__ import annotations
import math
import time
from PySide6.QtCore import QObject, QPointF, QRectF, QSize, Qt, QThread, QTimer, Signal, Slot
from PySide6.QtGui import QCursor, QImage, QImageReader, QPainter, QTransform
from PySide6.QtWidgets import QWidget
from aicompanion.characters import CharacterPackError
from aicompanion.characters.animation_pack import load_animation_pack

# 保留本批 384 像素原帧，避免高 DPI 显示时二次放大变软；过渡时只预载下一段，隐藏后释放。
FRAME_HEIGHT = 384
FADE = .22
ENTRY_FADE = .10
URGENT_FADE = .10


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


def mix_frames(previous,current,amount):
    """线性相加预乘颜色，避免 SourceOver 双层淡入导致身体半透明。"""
    if amount<=0:return previous
    if amount>=1:return current
    def weighted(source,weight):
        image=QImage(current.size(),QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter=QPainter(image);painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setOpacity(weight);painter.drawImage(image.rect(),source);painter.end()
        return image
    image=weighted(previous,1-amount);overlay=weighted(current,amount)
    # Plus 使用全覆盖；直接把全局 opacity 用在 Plus 上会再次衰减已有目标像素。
    painter=QPainter(image);painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Plus)
    painter.drawImage(image.rect(),overlay);painter.end()
    return image



class AnimationAvatar(QWidget):
    poseShown = Signal(str)
    landed = Signal()
    frameAdvanced = Signal(int)
    failed = Signal(str)
    transitionStarted = Signal(str,str)
    transitionFinished = Signal(str,str)
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
        self.dragging=False; self.walking=0; self._walk_vertical=0.; self._gait_distance=0.; self.direction=1 if self.pack.actions[self.pack.poses['idle']].facing=='right' else -1; self._cue=None; self._cue_until=0.; self._cue_duration=0.; self._cue_started=False
        self._sheet=QImage(); self._frame=self.poster; self._previous=QImage(); self._display=self.poster
        self._frame_facing=self.pack.actions[self.pack.poses['idle']].facing
        self._gait_loaded=False; self._bridge=None; self._bridge_sheet=QImage(); self._bridge_started=0.; self._bridge_index=-1
        self._queue=[]; self._ready_next=None; self._fade_duration=FADE; self._outgoing=None
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
        elif self._cue is not None and now<self._cue_until and self.pose not in ('error','wait','dizzy'):
            key=self._cue
        elif self.pose=='idle' and self.walking:
            key=self._walking_key()
        else:
            key=self.pack.poses.get(self.pose,self.pack.poses['idle'])
        self.action=key
        if key!=self._requested:
            # 被打断时截取正在显示的合成结果；不能回闪到前一个动作的原图。
            source=self._loaded if self._bridge is None and self._previous.isNull() else None
            if self._bridge is not None or not self._previous.isNull():
                self._frame=self._display.copy(); self._sheet=QImage(); self._loaded=None
                self._previous=QImage(); self._outgoing=None; self._bridge=None; self._bridge_sheet=QImage()
            self._queue=[]; self._ready_next=None; self._requested=key
            if self._loaded!=key:
                urgent=self.pose in ('grabbed','falling') or self.dragging
                route=[] if urgent else self.pack.transition_route(source,key)
                self._queue=[(item.clip,item) for item in route]+[(self.pack.actions[key],None)]
        self._ensure_loading()

    def _ensure_loading(self):
        if self._closed or not self.isVisible() or self._loading is not None or self._ready_next is not None:return
        if not self._queue:
            if self._bridge is None and self._loaded!=self._requested and self._requested is not None:
                self._queue=[(self.pack.actions[self._requested],None)]
            else:return
        clip,_=self._queue[0]; self._loading=clip.id; self.decodeRequested.emit(clip)

    @Slot(object,QImage,str)
    def _decoded(self,action,image,error):
        if self._loading==action.id:self._loading=None
        if self._closed or not self.isVisible():return
        if not self._queue or action.id!=self._queue[0][0].id:
            self._ensure_loading(); return
        if error:
            self._queue=[]; self.failed.emit(error); return
        stage=self._queue.pop(0); self._ready_next=(stage,image)
        if self._bridge is None:self._consume_ready(time.monotonic())
        self._ensure_loading()

    def _consume_ready(self,now):
        if self._ready_next is None:return
        (clip,transition),image=self._ready_next; self._ready_next=None
        outgoing=None
        if self._bridge is None and self._loaded is not None and self._previous.isNull() and not self._sheet.isNull():
            old=self.pack.actions[self._loaded]
            gait=self._gait_loaded or (self.walking and self._loaded==self._walking_key())
            elapsed=max(0.,self._gait_distance/old.cycle_distance if gait else now-self._started)
            outgoing=(old,self._sheet,self._started,elapsed,gait)
        previous=self._display.copy()
        if self._frame_facing!=clip.facing:previous=previous.mirrored(True,False)
        was_bridge=self._bridge is not None
        self._previous=previous; self._outgoing=outgoing; self._frame_facing=clip.facing
        self._fade_started=now
        if transition is not None:
            self._sheet=QImage(); self._loaded=None; self._gait_loaded=False; self._bridge=transition; self._bridge_sheet=image
            self._bridge_started=now; self._bridge_index=-1; self._fade_duration=ENTRY_FADE
            self.transitionStarted.emit(transition.source,transition.target)
        else:
            self._bridge=None; self._bridge_sheet=QImage(); self._sheet=image; self._loaded=clip.id
            phase=0.
            if outgoing and outgoing[0].blend_group and outgoing[0].blend_group==clip.blend_group:
                old,_,_,elapsed,_=outgoing;phase=(elapsed*old.fps%old.count)/old.count
            self._started=now-phase*clip.count/clip.fps; self._index=-1
            self._gait_loaded=bool(self.walking and clip.id==self._walking_key())
            if self._gait_loaded:self._gait_distance=0.
            if self._cue==clip.id and not self._cue_started:
                self._cue_until=now+self._cue_duration;self._cue_started=True
            self._fade_duration=ENTRY_FADE if was_bridge else (URGENT_FADE if self.pose in ('grabbed','falling') or self.dragging else FADE)
        self.advance()

    def _take_frame(self,sheet,clip,index):
        w=sheet.width()//clip.columns;h=sheet.height()//clip.rows
        self._frame=sheet.copy(index%clip.columns*w,index//clip.columns*h,w,h)
        self.frameAdvanced.emit(index)

    def _compose(self,now):
        if self._previous.isNull():self._display=self._frame;return
        progress=min(1.,max(0.,(now-self._fade_started)/self._fade_duration))
        amount=progress*progress*(3-2*progress)
        if self._outgoing is not None:
            clip,sheet,started,elapsed,gait=self._outgoing
            index=clip.frame_at(elapsed if gait else now-started)
            w=sheet.width()//clip.columns;h=sheet.height()//clip.rows
            self._previous=sheet.copy(index%clip.columns*w,index//clip.columns*h,w,h)
            if clip.facing!=self._frame_facing:self._previous=self._previous.mirrored(True,False)
        self._display=mix_frames(self._previous,self._frame,amount)
        if progress>=1:self._previous=QImage();self._outgoing=None

    @property
    def transitioning(self):return self._bridge is not None or not self._previous.isNull()

    def advance(self):
        if self._closed:return
        now=time.monotonic()
        if self._cue is not None and now>=self._cue_until:
            self._cue=None; self._select()
        changed=False
        if self._bridge is not None:
            clip=self._bridge.clip; age=now-self._bridge_started
            index=min(clip.count-1,int(max(0.,age)*clip.fps))
            if index!=self._bridge_index:
                self._bridge_index=index;self._take_frame(self._bridge_sheet,clip,index);changed=True
            if age>=clip.count/clip.fps and self._ready_next is not None:
                finished=self._bridge;self._compose(now);self._consume_ready(now)
                self.transitionFinished.emit(finished.source,finished.target)
                self._ensure_loading();return
        elif self._loaded is not None:
            clip=self.pack.actions[self._loaded]
            gait=self._gait_loaded or (self.walking and self._loaded==self._walking_key())
            index=clip.frame_at(self._gait_distance/clip.cycle_distance) if gait else clip.frame_at(now-self._started)
            if index!=self._index:
                self._index=index;self._take_frame(self._sheet,clip,index);changed=True
        if self._hop_started>=0 and now-self._hop_started>=self._hop_duration:
            self._hop_started=-1.;self.landed.emit();changed=True
        fading=not self._previous.isNull()
        if changed or fading:self._compose(now)
        if changed or fading or self.hopping or now-self._bounce_started<.4:self.update()

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
        mirror=self.direction!=(1 if self._frame_facing=='right' else -1)
        t.scale((-1 if mirror else 1)*(1+squash),1-squash)
        t.translate(-rect.center().x(),-rect.bottom())
        return t

    def paintEvent(self,event):
        painter=QPainter(self); painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_Source)
        painter.fillRect(self.rect(),Qt.GlobalColor.transparent)
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        painter.setTransform(self._transform())
        painter.drawImage(self.content_rect(),self._display);painter.end()

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
        px=min(self._display.width()-1,int(x*self._display.width()))
        py=min(self._display.height()-1,int(y*self._display.height()))
        return px>=0 and py>=0 and self._display.pixelColor(px,py).alpha()>40

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

    def _walking_key(self):
        pose='walking_back' if self._walk_vertical<-.15 else 'walking'
        return self.pack.poses.get(pose,self.pack.poses.get('walking','idle'))

    @property
    def walking_ready(self):
        return bool(self.walking and self._loaded==self._walking_key() and not self.transitioning)

    def set_walking_vector(self,dx,dy):
        length=math.hypot(dx,dy)
        self.walking=(1 if dx>=0 else -1) if length else 0
        self._walk_vertical=dy/length if length else 0.
        if abs(dx)>1e-6:self.face(dx)
        self._select()

    def step_walking(self,distance):
        # 路线实际走多远才推进多少步态；加载、等待、暂停时脚不会原地空转。
        if self.walking_ready:
            self._gait_distance+=max(0.,distance);self.advance()

    def set_walking(self,direction):
        self.set_walking_vector(direction,0)


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
                 'smug':'smug','shy':'shy','surprised':'surprised',
                 'pout':'pout','milk_tea':'milk_tea','stretch':'stretch','sleep':'sleepy','dizzy':'error','drag':'grabbed'}
        pose=mapping.get(name)
        fallback={'smug':'patted','shy':'patted','surprised':'poked','pout':'poked','milk_tea':'idle','stretch':'idle'}
        if pose not in self.pack.poses:pose=fallback.get(pose,pose)
        if name=='neutral':self._cue=None;self._select();return
        if pose in self.pack.poses:
            self._cue=self.pack.poses[pose]; self._cue_duration=max(2.2,self.pack.actions[self._cue].count/self.pack.actions[self._cue].fps);self._cue_started=False
            self._cue_until=time.monotonic()+3.7; self._select()
        elif kind=='motion': self.bounce(.8)

    def play_action(self,key):
        if key not in self.pack.actions or self.pose in ('grabbed','falling'): return
        self._cue=key; action=self.pack.actions[key]
        self._cue_duration=action.count/action.fps;self._cue_started=False
        self._cue_until=time.monotonic()+self._cue_duration+1.5; self._select()

    def showEvent(self,event):
        super().showEvent(event)
        if not self._closed: self.timer.start(); self._select()

    def hideEvent(self,event):
        self.timer.stop(); self._sheet=QImage(); self._loaded=None; self._previous=QImage()
        self._bridge=None; self._bridge_sheet=QImage(); self._outgoing=None; self._ready_next=None; self._queue=[]
        super().hideEvent(event)

    def shutdown(self):
        if self._closed: return
        self._closed=True; self.timer.stop(); self.thread.quit(); self.thread.wait()
        self._sheet=QImage(); self._previous=QImage(); self._bridge_sheet=QImage(); self._outgoing=None; self._ready_next=None; self._queue=[]
