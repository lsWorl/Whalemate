"""真实 Qt 窗口内冻结切换边界，比较头顶/鞋底的纵向像素位置。"""
from pathlib import Path
from types import SimpleNamespace
import argparse,json
from PIL import Image,ImageDraw,ImageFont
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication
from aicompanion.body.rig_view import RigAvatar
from aicompanion.body.webengine_flags import apply_webengine_flags
from aicompanion.characters import load_pack
from aicompanion.ui.desktop_pet import DesktopPetWindow
from preview_motion_frames import PreviewSettings,grab_rgba,visible_pixels

ROOT=Path(__file__).resolve().parents[1]
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--out',type=Path,required=True);args=parser.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    apply_webengine_flags();app=QApplication([]);app.setQuitOnLastWindowClosed(False)
    pet=DesktopPetWindow(PreviewSettings());view=RigAvatar(ROOT/'src/aicompanion/characters/deepseek-whale/rig/rig.json')
    pet.set_view(view,'rig',load_pack(ROOT/'src/aicompanion/characters/deepseek-whale'));pet._life_timer.stop();pet._input_timer.stop();pet._state_timer.stop();pet.show();pet.move(700,200);pet.raise_()
    scales=[1.,1.5];records=[];errors=[];current={};attempt=0
    def js(code,callback):view.page().runJavaScript(code,callback)
    def capture(name,after):
        def save():
            image=grab_rgba(pet);image.save(args.out/f'{pet.scale:g}x-{name}.png')
            box=image.getchannel('A').point(lambda a:255 if a>=128 else 0).getbbox()
            if not box or visible_pixels(image)<100:errors.append('角色不可见：'+name);app.exit(2);return
            current[name]={'head_y':box[1],'feet_y':box[3]-1,'bbox':list(box),'image':str(args.out/f'{pet.scale:g}x-{name}.png')};after()
        QTimer.singleShot(80,save)
    def rendered(after):js("engine.tick(0);app.renderer.render(app.stage);true",lambda _:after())
    def idle_before():
        js("app.ticker.stop();rigApi.set({pose:'idle',walking:0,facing:1});engine.tick(0);app.renderer.render(app.stage);true",
           lambda _:capture('idle-before',start))
    def start():
        js("rigApi.set({walking:1});engine.tick(0);true",lambda _:wait_start())
    def wait_start():
        nonlocal attempt
        attempt+=1
        def received(raw):
            if json.loads(raw)['displayed']=='start':rendered(lambda:capture('start-first',brake))
            elif attempt<100:QTimer.singleShot(20,wait_start)
            else:fail('start未就绪')
        js("engine.tick(0);JSON.stringify(rigApi.diagnostics().motion)",received)
    def brake():
        js("rigApi.set({walking:0});engine.tick(0);JSON.stringify(rigApi.diagnostics().motion)",lambda _:wait_brake())
    def wait_brake():
        nonlocal attempt
        attempt+=1
        def received(raw):
            if json.loads(raw)['displayed']=='brake':rendered(lambda:capture('brake-first',brake_last))
            elif attempt<100:QTimer.singleShot(20,wait_brake)
            else:fail('brake未就绪')
        js("engine.tick(0);JSON.stringify(rigApi.diagnostics().motion)",received)
    def brake_last():
        js("for(let i=0;i<100 && rigApi.diagnostics().motion.index<rigApi.diagnostics().motion.count-1;i++)engine.tick(10);engine.tick(0);app.renderer.render(app.stage);true",
           lambda _:capture('brake-last',idle_after))
    def idle_after():
        js("for(let i=0;i<100 && rigApi.diagnostics().motion.displayed!=='idle';i++)engine.tick(10);app.renderer.render(app.stage);true",
           lambda _:capture('idle-after',done_scale))
    def fail(reason):
        errors.append(reason);(args.out/'errors.json').write_text(json.dumps(errors,ensure_ascii=False),encoding='utf-8');print(reason,flush=True);app.exit(2)
    def done_scale():
        changes={}
        for label,a,b in [('idle_to_start','idle-before','start-first'),('brake_to_idle','brake-last','idle-after')]:
            changes[label]={'head_delta':current[b]['head_y']-current[a]['head_y'],'feet_delta':current[b]['feet_y']-current[a]['feet_y']}
        records.append({'scale':pet.scale,'window_size':[pet.width(),pet.height()],'captures':dict(current),'transitions':changes,
            'passed':all(abs(v)<=2 for change in changes.values() for v in change.values())})
        if scales:next_scale()
        else:
            result={'method':'真实QWidget.grab，WebGL冻结动作边界；alpha>=128可见轮廓的头顶/鞋底纵向像素','scales':records,'errors':errors,'passed':all(r['passed'] for r in records) and not errors}
            (args.out/'alignment.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');print(json.dumps(result,ensure_ascii=False));app.exit(0 if result['passed'] else 1)
    def next_scale():
        nonlocal current,attempt
        current={};attempt=0;pet.set_scale(scales.pop(0));QTimer.singleShot(180,idle_before)
    def ready(value):
        if value=='ready':QTimer.singleShot(1200,next_scale)
        elif value.startswith('error:'):fail(value)
    view.bridge.loaded.connect(ready);QTimer.singleShot(30000,lambda:fail('校验超时'))
    code=app.exec();pet.shutdown();return code
if __name__=='__main__':raise SystemExit(main())
