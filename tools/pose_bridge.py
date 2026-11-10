"""大姿态补间先对齐头部和脚底轮廓，避免两张不同高度的脸重叠。"""
from __future__ import annotations
import numpy as np
from PIL import Image
from animation_inbetween import components,rgba_float,rgba_image,sample


def anchors(image):
    rgba=np.asarray(image);alpha=rgba[:,:,3]>40;ys,xs=np.where(alpha)
    if not len(ys):raise ValueError('过渡端点没有可见角色。')
    top,bottom=int(ys.min()),int(ys.max())
    rgb=rgba[:,:,:3].astype(np.int16)
    skin=alpha&(rgb[:,:,0]>170)&(rgb[:,:,1]>90)&(rgb[:,:,2]>70)&(rgb[:,:,0]>rgb[:,:,1]+8)&(rgb[:,:,0]>rgb[:,:,2]+12)
    skin[int(image.height*.72):]=False
    parts=components(skin)
    chin=parts[0][0][3] if parts and parts[0][1]>20 else round(top+(bottom-top)*.48)
    chin=max(top+4,min(bottom-8,chin))
    return np.array([0,top,chin,bottom,image.height-1],dtype=np.float32)


def row_bounds(image):
    mask=np.asarray(image)[:,:,3]>40;h,w=mask.shape
    has=mask.any(1);indices=np.flatnonzero(has)
    left=np.argmax(mask,axis=1).astype(np.float32)
    right=(w-1-np.argmax(mask[:,::-1],axis=1)).astype(np.float32)
    left=np.interp(np.arange(h),indices,left[indices]);right=np.interp(np.arange(h),indices,right[indices])
    # 柔化呆毛、手指等轮廓处的窄区段，避免横向压成细线。
    kernel=np.ones(9,dtype=np.float32)/9
    left=np.convolve(np.pad(left,4,mode='edge'),kernel,mode='valid')
    right=np.convolve(np.pad(right,4,mode='edge'),kernel,mode='valid')
    return left,np.maximum(right,left+8)


def prepare(first,second):
    return anchors(first),anchors(second),row_bounds(first),row_bounds(second)


def pose_interpolate(first,second,t,geometry=None,soft=False):
    if t<=0:return first.copy()
    if t>=1:return second.copy()
    aa,bb,ab,bc=geometry or prepare(first,second)
    middle=aa*(1-t)+bb*t
    h,w=first.height,first.width;yy,xx=np.mgrid[:h,:w].astype(np.float32)
    y_a=np.interp(np.arange(h),middle,aa);y_b=np.interp(np.arange(h),middle,bb)
    la=np.interp(y_a,np.arange(h),ab[0]);ra=np.interp(y_a,np.arange(h),ab[1])
    lb=np.interp(y_b,np.arange(h),bc[0]);rb=np.interp(y_b,np.arange(h),bc[1])
    left=la*(1-t)+lb*t;right=ra*(1-t)+rb*t
    u=(xx-left[:,None])/np.maximum(8,right-left)[:,None]
    xa=la[:,None]+u*(ra-la)[:,None];xb=lb[:,None]+u*(rb-lb)[:,None]
    a=sample(rgba_float(first),xa-xx,y_a[:,None]-yy)
    b=sample(rgba_float(second),xb-xx,y_b[:,None]-yy)
    # 逐格动画保留一张原画的线条，避免脸、手指和蕾丝双影；几何位置仍连续移动。
    if not soft:return rgba_image(a if t<.5 else b)
    weight=np.clip((t-.30)/.40,0,1);weight=weight*weight*(3-2*weight)
    return rgba_image(a*(1-weight)+b*weight)
