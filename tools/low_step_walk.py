"""用自绘低步幅图纸的独立腿部做离线小步运动，保持近直膝和清晰线条。"""
import math
import numpy as np
from PIL import Image
from animation_inbetween import components,rgba_float,rgba_image,sample


def leg_layers(image):
    alpha=np.asarray(image)[:,:,3]>40;h,w=alpha.shape
    for cut in range(round(h*.69),round(h*.84)):
        mask=alpha.copy();mask[:cut]=False
        parts=[p for p in components(mask) if p[1]>80]
        if len(parts)!=2:continue
        if any(b[3]<h*.87 or b[1]>cut+1 or b[2]-b[0]>w*.3 for b,_,_ in parts):continue
        parts.sort(key=lambda p:p[0][0])
        layers=[]
        for box,area,mask in parts:
            a=np.array(image);a[:,:,3]=(a[:,:,3].astype(np.uint16)*mask//255).astype(np.uint8)
            layers.append(Image.fromarray(a))
        return cut,layers
    raise ValueError('低步幅图纸需要裙摆下方两条分离的完整小腿。')


def low_step_frames(frames):
    cut,legs=leg_layers(frames[0]);h,w=frames[0].height,frames[0].width
    yy=np.mgrid[:h,:w][0].astype(np.float32)
    ground=max(int(np.where(np.asarray(leg)[:,:,3]>40)[0].max()) for leg in legs)
    normalized=[]
    for leg in legs:
        bottom=int(np.where(np.asarray(leg)[:,:,3]>40)[0].max())
        source_y=cut+(yy-cut)*(bottom-cut)/max(1,ground-cut)
        normalized.append(rgba_image(sample(rgba_float(leg),np.zeros_like(yy),source_y-yy)))
    legs=normalized
    falloff=np.clip((yy-cut)/max(1,h*.923-cut),0,1)
    result=[]
    for i,frame in enumerate(frames):
        body=np.array(frame);body[cut:,:,3]=0;canvas=Image.fromarray(body)
        for j,leg in enumerate(legs):
            phase=i/len(frames)*math.tau+j*math.pi
            shift=10*math.cos(phase);lift=5*max(0.,math.sin(phase))
            moved=rgba_image(sample(rgba_float(leg),-shift*falloff,lift*falloff))
            canvas.alpha_composite(moved)
        result.append(canvas)
    return result,{'kind':'separated_low_step_legs','cut_y':cut,'max_horizontal_px':10,'max_lift_px':5,
                   'cycle_distance_at_300px':32,'near_straight_knee':True}
