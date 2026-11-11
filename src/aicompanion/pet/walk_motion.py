"""桌面窗口的水平速度曲线；帧相位仍由网页按实际位移推进。"""
from dataclasses import dataclass
from math import isfinite,sqrt

@dataclass
class WalkMotion:
    position: float
    target: float
    scale: float = 1.0
    speed: float = 0.0

    def __post_init__(self):
        self.scale=max(.1,float(self.scale))
        # 既有漫游只取80..260px；240px让长端能跑，保持原触发频率和选点范围不变。
        self.cruise=(160.0 if abs(self.target-self.position)>240 else 70.0)*self.scale
        self.acceleration=360.0*self.scale
        self.deceleration=480.0*self.scale

    @property
    def done(self):return abs(self.target-self.position)<1e-7

    def advance(self,seconds):
        if not isfinite(seconds) or seconds<=0 or self.done:return self.position
        dt=min(.05,seconds);remaining=self.target-self.position;distance=abs(remaining)
        # 用剩余刹车距离约束速度；保留浮点位置，避免低速每帧不足半像素时卡在原地。
        # 离散一帧的行程也占刹车距离：解 v_next²/(2a)+(v+v_next)dt/2<=剩余距离。
        a=self.deceleration;disc=max(0.,(a*dt)**2-4*(a*dt*self.speed-2*a*distance))
        desired=min(self.cruise,max(0.,(sqrt(disc)-a*dt)/2))
        rate=self.acceleration if desired>=self.speed else self.deceleration
        previous=self.speed;self.speed+=max(-rate*dt,min(rate*dt,desired-self.speed))
        step=min(distance,(previous+self.speed)*.5*dt)
        self.position+=step if remaining>0 else -step
        if step>=distance:self.position=self.target;self.speed=0.0
        return self.position
