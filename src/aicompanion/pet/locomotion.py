"""桌面平面行走：累计浮点位置，让斜走与低帧率的速度保持一致。"""
from __future__ import annotations
import math


def clamp_target(target,bounds,size):
    x,y=target;left,top,width,height=bounds;w,h=size
    return (max(left,min(float(x),left+max(0,width-w))),
            max(top,min(float(y),top+max(0,height-h))))


def advance_walk(position,target,distance):
    dx,dy=target[0]-position[0],target[1]-position[1]
    remaining=math.hypot(dx,dy);step=min(remaining,max(0.,distance))
    if remaining<=step or remaining<1e-6:return tuple(target),remaining,True
    return (position[0]+dx/remaining*step,position[1]+dy/remaining*step),step,False


def braking_step(speed,remaining,dt,scale=1.,speed_limit=42.):
    if remaining<=.3:return remaining,0.
    acceleration=180*scale
    target=min(speed_limit*scale,math.sqrt(2*acceleration*remaining))
    change=acceleration*max(0.,dt)
    next_speed=max(target,speed-change) if speed>target else min(target,speed+change)
    return min(remaining,(speed+next_speed)*.5*dt),next_speed
