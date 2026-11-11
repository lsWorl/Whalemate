"""离线回放桌宠公共事件，抓取真实 Qt 窗口；不读取用户数据库、不模拟系统鼠标。"""
from __future__ import annotations
import argparse,json,time
from pathlib import Path
from types import SimpleNamespace
from PIL import Image,ImageDraw,ImageFont
from PySide6.QtCore import QTimer
from PySide6.QtGui import QImage
from PySide6.QtWidgets import QApplication
from aicompanion.body.webengine_flags import apply_webengine_flags
from aicompanion.body.rig_view import RigAvatar
from aicompanion.ui.desktop_pet import DesktopPetWindow

ROOT=Path(__file__).resolve().parents[1]

class PreviewSettings:
    def __init__(self):self.values={'pet_bubble_enabled':False,'pet_hide_fullscreen':False,'pet_wander':False,'desktop_pet_scale':1.0}
    def get_setting(self,key,default=None):return self.values.get(key,default)
    def set_setting(self,key,value):self.values[key]=value

def grab_rgba(widget):
    qimage=widget.grab().toImage().convertToFormat(QImage.Format.Format_RGBA8888)
    return Image.frombytes('RGBA',(qimage.width(),qimage.height()),bytes(qimage.constBits()),'raw','RGBA',qimage.bytesPerLine())

def visible_pixels(image):
    rgba=image.tobytes();return sum(rgba[i+3]>100 and rgba[i+2]>rgba[i]+15 for i in range(0,len(rgba),4))

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);parser.add_argument('--frame0-only',action='store_true')
    parser.add_argument('--fps',type=int,choices=(15,30),default=30,help='录制采样目标帧率，真实时间写入 GIF')
    args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True);apply_webengine_flags()
    app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    pet=DesktopPetWindow(PreviewSettings());view=RigAvatar(ROOT/'src/aicompanion/characters/deepseek-whale/rig/rig.json')
    pet.set_view(view,'rig',SimpleNamespace(display_height=320));pet._life_timer.stop();pet._input_timer.stop();pet.input_locked=True
    pet.show();pet.raise_();screen=pet.screen().availableGeometry();ground=pet._ground_y();origin=screen.left()+screen.width()//2-160
    pet.move(origin,ground);records=[];frames=[];diagnostics=[];closeups={};errors=[];started=None;last=None;phase=None;scene='drag';position=float(origin)
    font=ImageFont.truetype('C:/Windows/Fonts/msyh.ttc',20)
    timer=QTimer();timer.setInterval(round(1000/args.fps))
    def diagnostic(raw):
        try:diagnostics.append(json.loads(raw))
        except (TypeError,ValueError):errors.append('诊断回传格式错误')
    def save_scene():
        if frames:
            seen={d.get('motion',{}).get('displayed') for d in diagnostics}
            required={'pickup','drag_left','drag_right','release','fall','land','dizzy','recover'} if scene=='drag' else {'start','walk','run','brake','turn'}
            if required-seen:errors.append('录制缺动作：'+','.join(sorted(required-seen)))
            if any(d.get('motion',{}).get('visible_character_layers')!=1 or d.get('motion',{}).get('layer_alpha')!=1
                   or d.get('motion',{}).get('fading') for d in diagnostics):errors.append('发现透明叠放或角色层数量不为1')
            name='runtime-drag' if scene=='drag' else 'runtime-locomotion'
            durations=[max(10,round((records[i+1]['t']-records[i]['t'])*100)*10) for i in range(len(records)-1)]+[70]
            frames[0].save(args.out/(name+'.gif'),save_all=True,append_images=frames[1:],duration=durations,loop=0,disposal=2)
            (args.out/(name+'-diagnostics.json')).write_text(json.dumps({'method':'真实 DesktopPetWindow QWidget.grab，按窗口实际位置合成浅灰录制舞台；公共事件回放，无系统鼠标注入',
                'fps_target':args.fps,'opaque_checks_passed':not errors,'frames':records,'diagnostics':diagnostics,'errors':errors},ensure_ascii=False,indent=2),encoding='utf-8')
            for label,samples in closeups.items():
                images=[]
                for sample_time,image in samples:
                    bg=Image.new('RGBA',(pet.width(),pet.height()+35),'#edf1f6');bg.alpha_composite(image)
                    ImageDraw.Draw(bg).text((8,pet.height()+3),f'{label} {sample_time:.3f}s',font=font,fill='#263b60');images.append(bg.convert('RGB'))
                durations=[max(10,round((samples[i+1][0]-samples[i][0])*100)*10) for i in range(len(samples)-1)]+[40]
                images[0].save(args.out/f'runtime-{label}-closeup.gif',save_all=True,append_images=images[1:],duration=durations,loop=0,disposal=2)
                proof=Image.new('RGB',(images[0].width*len(images),images[0].height),'#edf1f6')
                for i,image in enumerate(images):proof.paste(image,(i*image.width,0))
                proof.save(args.out/f'runtime-{label}-all-frames.png')
    def frame0():
        nonlocal started,last
        image=grab_rgba(pet);blue=visible_pixels(image);image.save(args.out/'runtime-frame0.png')
        (args.out/'runtime-frame0.json').write_text(json.dumps({'visible_blue_pixels':blue,'corner_alpha':image.getpixel((1,1))[3],
            'window_geometry':[pet.x(),pet.y(),pet.width(),pet.height()],'method':'真实 Qt 窗口抓图'},ensure_ascii=False,indent=2),encoding='utf-8')
        if blue<100 or image.getpixel((1,1))[3]!=0:
            errors.append('第0帧看不到透明背景中的角色，停止录制');app.exit(2);return
        if args.frame0_only:app.exit(0);return
        started=last=time.monotonic();timer.start()
    def tick():
        nonlocal started,last,phase,scene,position,frames,records,diagnostics,closeups
        now=time.monotonic();t=now-started;dt=now-last;last=now
        if scene=='drag':
            desired='idle' if t<.7 else 'left' if t<2.2 else 'right' if t<3.7 else 'fall'
            if desired!=phase:
                phase=desired
                if desired=='left':pet.dispatch('grab',active=True);view.set_drag(True,-300);pet.move(pet.x(),ground-500)
                elif desired=='right':view.set_drag(True,300)
                elif desired=='fall':view.set_drag(False);pet.start_fall(80,0)
            if desired in ('left','right'):
                position+=(-110 if desired=='left' else 110)*dt;pet.move(round(position),ground-500)
        else:
            desired='idle' if t<.7 else 'walk' if t<2.7 else 'run' if t<4.1 else 'brake' if t<4.7 else 'turn' if t<5.1 else 'left' if t<6.5 else 'stop'
            if desired!=phase:
                phase=desired
                if desired in ('walk','run'):view.set_walking(1)
                elif desired in ('brake','stop'):view.set_walking(0)
                elif desired=='turn':view.face(-1)
                elif desired=='left':view.set_walking(-1)
            if desired in ('walk','run','left'):
                speed=190 if desired=='run' else -70 if desired=='left' else 70;old=pet.x();position+=speed*dt;pet.move(round(position),ground)
                view.advance_motion(pet.x()-old,dt)
        image=grab_rgba(pet);blue=visible_pixels(image)
        intervals={'pickup-transition':(.65,1.3),'drag-reversal':(2.1,2.65)} if scene=='drag' else {'stop-transition':(6.3,7.1)}
        for label,(begin,end) in intervals.items():
            if begin<=t<=end:closeups.setdefault(label,[]).append((t,image.copy()))
        if blue<100:errors.append(f'{scene} {t:.3f}s 角色不可见')
        height=820 if scene=='drag' else 680;floor=height-32
        stage=Image.new('RGB',(1000,height),'#edf1f6');stage.paste(image,(round(500+(pet.x()-origin)-pet.width()/2),round(floor+2-pet.height()+pet.y()-ground)),image)
        draw=ImageDraw.Draw(stage);draw.line((20,floor,980,floor),fill='#b8c4d4',width=2);draw.text((18,14),f'{scene}  {t:.2f}s  {desired}  /  Qt实际窗口',font=font,fill='#263b60')
        frames.append(stage);records.append({'t':round(t,4),'event_phase':desired,'x':pet.x(),'y':pet.y(),'visible_blue_pixels':blue})
        view.page().runJavaScript('JSON.stringify(rigApi.diagnostics())',lambda raw,target=diagnostics: target.append(json.loads(raw)))
        if t>7.6:
            save_scene()
            if scene=='drag':
                scene='locomotion';frames=[];records=[];diagnostics=[];closeups={};phase=None;position=float(origin-240);pet.move(round(position),ground)
                pet.machine.snapshot=type(pet.machine.snapshot)();pet.dispatch('force_pose',pose='idle',seconds=.1);view.face(1);started=last=time.monotonic()
            else:timer.stop();app.exit(1 if errors else 0)
    timer.timeout.connect(tick)
    def ready(value):
        if value=='ready':QTimer.singleShot(900,frame0)
        elif value.startswith('error:'):errors.append(value);app.exit(2)
    view.bridge.loaded.connect(ready);QTimer.singleShot(45000,lambda:app.exit(3))
    code=app.exec();pet.shutdown();return code

if __name__=='__main__':raise SystemExit(main())
