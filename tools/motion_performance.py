"""性能测量用的真实桌宠事件回放；不注入系统鼠标，不打开用户数据库。"""
import json,math,time
from pathlib import Path
from PySide6.QtCore import QObject,QTimer

class MotionPerformance(QObject):
    def __init__(self,pet,output:Path,parent=None):
        super().__init__(parent);self.pet=pet;self.output=output;self.started=0.;self.phase=None;self.records=[];self.ticks=0
        self.timer=QTimer(self);self.timer.setInterval(40);self.timer.timeout.connect(self.tick)

    def start(self):
        self.started=time.monotonic();self.pet._life_timer.stop();self.pet.input_locked=True;self.pet.database.set_setting('pet_hide_fullscreen',False)
        self.timer.start()

    def route(self,distance):
        pet=self.pet;screen=pet.screen().availableGeometry();left=screen.left();right=screen.right()-pet.width()+1
        direction=-1 if pet.x()-left>right-pet.x() else 1
        pet.walk_to(round(max(left,min(right,pet.x()+direction*distance*pet.scale))))

    def tick(self):
        pet=self.pet;elapsed=time.monotonic()-self.started;cycle=elapsed%12
        phase='drag' if cycle<2 else 'fall' if cycle<3.5 else 'walk' if cycle<7 else 'run'
        if phase!=self.phase:
            self.phase=phase
            if phase=='drag':
                pet.stop_walking();pet._stop_fall();pet.dispatch('grab',active=True)
            elif phase=='fall':
                pet.view.set_drag(False);pet.dispatch('grab',active=False);pet.start_fall(30,0)
            elif phase=='walk':self.route(150)
            else:self.route(500)
        if phase=='drag':
            screen=pet.screen().availableGeometry();x=screen.center().x()-pet.width()/2+90*math.sin(cycle*math.pi)
            pet.move(round(x),pet._ground_y()-360);pet.view.set_drag(True,-300 if cycle<1 else 300)
        self.ticks+=1
        if self.ticks%5==0 and hasattr(pet.view,'page'):
            pet.view.page().runJavaScript('JSON.stringify(rigApi.diagnostics())',
                lambda raw,t=elapsed:self.record(t,raw))

    def record(self,elapsed,raw):
        try:self.records.append({'seconds':round(elapsed,3),'visible':self.pet.isVisible(),'motion':json.loads(raw).get('motion')})
        except (TypeError,ValueError):pass

    def save(self):
        self.timer.stop();self.output.parent.mkdir(parents=True,exist_ok=True)
        states=list(dict.fromkeys(r['motion']['displayed'] for r in self.records if r['motion']))
        self.output.write_text(json.dumps({'active_seconds':round(time.monotonic()-self.started,3),'states_seen':states,'records':self.records},ensure_ascii=False,indent=2),encoding='utf-8')
