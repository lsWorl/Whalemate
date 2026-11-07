"""艾米莉娅自定义 2D 网格效果验收，不将其标为 Cubism。"""
import json
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication, QLabel, QToolButton, QLineEdit


def schedule_emilia_smoke_check(window, database, output):
    output.mkdir(parents=True, exist_ok=True)
    view=window.avatar; state={'errors':[], 'ready':False}
    view.bridge.loaded.connect(lambda value: state.update(ready=value=='animation-ready') if not value.startswith('error:') else state['errors'].append(value))
    window.speech.enabled=False; window.speak_toggle.setChecked(False)
    window.hide()  # v2：角色常驻，聊天面板隐藏

    def sample(key, filename=None):
        view.page().runJavaScript('JSON.stringify(window.avatarDiagnostics())',lambda raw:state.update({key:json.loads(raw) if raw else {}}))
        if key=='idle': state['first_frame_alpha']=window.pet_window.grab().toImage().pixelColor(0,0).alpha()
        if filename: window.pet_window.grab().save(str(output/filename))
    def pose(value): view.page().runJavaScript('window.avatarRigTest('+json.dumps(value)+')')
    QTimer.singleShot(1600,lambda:sample('idle','idle.png'))
    QTimer.singleShot(1900,lambda:pose({'blink':1,'mouth':0,'mood':'calm','freeze':True}))
    QTimer.singleShot(2400,lambda:sample('closed','blink.png'))
    QTimer.singleShot(2600,lambda:pose({'blink':0,'mouth':.9,'mood':'shy','freeze':True}))
    QTimer.singleShot(3300,lambda:sample('talking','talk.png'))
    QTimer.singleShot(3500,lambda:pose(None))
    QTimer.singleShot(4200,lambda:sample('moving1'))
    QTimer.singleShot(4900,lambda:sample('moving2'))

    def finish_preview():
        pet=window.pet_window
        state['model_only']=pet.layout().count()==1 and not any(widget.isVisible() for kind in (QLabel,QToolButton,QLineEdit) for widget in pet.findChildren(kind))
        state['chat_hidden']=not window.isVisible()
        state['native_alpha']=view.grab().toImage().pixelColor(0,0).alpha()
        pet.resize(420,650); pet.constrain_geometry()
        window._pet_subtitle('这句话仅保存在后台，不应产生字幕框。')
        state['model_only_after_subtitle']=pet.layout().count()==1  # 字幕走独立气泡窗口，不进入桌宠布局
    QTimer.singleShot(5100,finish_preview)
    QTimer.singleShot(5700,lambda:sample('final','pet.png'))
    QTimer.singleShot(6000,lambda:view.play_motion('wave'))
    QTimer.singleShot(6600,lambda:sample('wave','wave.png'))
    QTimer.singleShot(6900,lambda:view.play_motion('tilt'))
    QTimer.singleShot(7600,lambda:sample('tilt','tilt.png'))
    QTimer.singleShot(8000,lambda:view.play_motion('nod'))
    QTimer.singleShot(8700,lambda:sample('nod','nod.png'))
    QTimer.singleShot(9200,lambda:view.play_motion('shake'))
    QTimer.singleShot(9800,lambda:sample('shake','shake.png'))
    QTimer.singleShot(10100,lambda:view.play_motion('shy'))
    QTimer.singleShot(10800,lambda:sample('shy','shy.png'))
    QTimer.singleShot(11600,window.request_exit)

    def finish():
        idle,closed,talking,first,second,final=(state.get(key,{}) for key in ('idle','closed','talking','moving1','moving2','final'))
        state['renderer']='pixi-mesh-not-cubism'
        state['pixels_visible']=final.get('visiblePixels',0)>5000
        state['transparent']=state.get('native_alpha')==0 and state.get('first_frame_alpha')==0 and final.get('transparentPixels',0)>1000
        state['mesh_deformed']=first.get('animation',{}).get('maxDisplacement',0)>.5 and abs(first.get('animation',{}).get('maxDisplacement',0)-second.get('animation',{}).get('maxDisplacement',0))>.01
        state['blink_applied']=closed.get('animation',{}).get('blink',0)>.95 and closed.get('pixelHash')!=idle.get('pixelHash')
        state['automatic_blink']=second.get('animation',{}).get('blinkCount',0)>=1
        state['mouth_applied']=talking.get('mouth',0)>.8 and talking.get('pixelHash')!=closed.get('pixelHash')
        state['shy_applied']=talking.get('cheek',0)>.8
        state['animation_advancing']=second.get('animation',{}).get('frames',0)>first.get('animation',{}).get('frames',0)
        state['actions_driven']=all(state.get(name,{}).get('animation',{}).get('action')==name and state.get(name,{}).get('animation',{}).get('actionProgress',0)>.1 for name in ('wave','tilt','nod','shake','shy'))
        bounds=final.get('bounds',{})
        state['framed']=bounds.get('x',-100)>=-1 and bounds.get('y',-100)>=-1 and bounds.get('x',0)+bounds.get('width',10000)<=final.get('width',0)+1 and bounds.get('y',0)+bounds.get('height',10000)<=final.get('height',0)+1
        state['remaining_workers']=len(window.workers)+len(window.tasks)+len(window.speech.workers)
        required=['ready','model_only','model_only_after_subtitle','chat_hidden','pixels_visible','transparent','mesh_deformed',
            'blink_applied','automatic_blink','mouth_applied','shy_applied','animation_advancing','actions_driven','framed']
        state['passed']=all(state.get(key) for key in required) and not state['errors'] and state['remaining_workers']==0 and final.get('presentation')=='animated-illustration'
        (output/'report.json').write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(state,ensure_ascii=False,indent=2),flush=True)
    QApplication.instance().aboutToQuit.connect(finish)
    return state
