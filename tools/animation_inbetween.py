"""离线关键帧对齐和双向稠密光流补间；不属于桌宠运行依赖。"""
from __future__ import annotations
import numpy as np
from PIL import Image,ImageFilter


def components(mask):
    """按行区段合并连通像素，避免逐像素 Python 洪水填充的开销。"""
    parent=[]; boxes=[]; areas=[]; previous=[]; rows=[]
    def find(i):
        while parent[i]!=i:
            parent[i]=parent[parent[i]]; i=parent[i]
        return i
    for y,row in enumerate(mask):
        change=np.diff(np.pad(row.astype(np.int8),(1,1)))
        spans=list(zip(np.flatnonzero(change==1),np.flatnonzero(change==-1)))
        current=[]; pi=0
        for x0,x1 in spans:
            i=len(parent);parent.append(i);boxes.append([int(x0),y,int(x1),y+1]);areas.append(int(x1-x0))
            while pi<len(previous) and previous[pi][1]<x0:pi+=1
            j=pi
            while j<len(previous) and previous[j][0]<=x1:
                other=find(previous[j][2]);parent[other]=i;j+=1
            current.append((x0,x1,i));rows.append((y,x0,x1,i))
        previous=current
    totals={}
    for i,box in enumerate(boxes):
        key=find(i)
        if key not in totals:totals[key]=[box[:],0]
        b,total=totals[key];b[0]=min(b[0],box[0]);b[1]=min(b[1],box[1]);b[2]=max(b[2],box[2]);b[3]=max(b[3],box[3]);totals[key][1]+=areas[i]
    selected=sorted(totals,key=lambda k:totals[k][1],reverse=True)[:6]
    masks={k:np.zeros(mask.shape,dtype=np.uint8) for k in selected}
    for y,x0,x1,i in rows:
        key=find(i)
        if key in masks:masks[key][y,x0:x1]=255
    return [(totals[k][0],totals[k][1],masks[k]) for k in selected]


def extract_keys(sheet,size=384,horizontal_anchor='feet'):
    if horizontal_anchor not in ('feet','head'):raise ValueError('未知横向锚点。')
    image=Image.open(sheet).convert('RGBA');parts=components(np.asarray(image)[:,:,3]>40)
    if len(parts)!=6 or min(p[1] for p in parts)<1000:raise ValueError('补帧图必须包含六个独立完整角色。')
    parts.sort(key=lambda p:((p[0][1]+p[0][3])/2>image.height/2,(p[0][0]+p[0][2])/2))
    height=float(np.median([p[0][3]-p[0][1] for p in parts]));scale=size*.835/height
    frames=[];report=[]
    for box,area,mask in parts:
        if box[0]<=1 or box[1]<=1 or box[2]>=image.width-1 or box[3]>=image.height-1:
            raise ValueError('补帧主角色触碰整张图边缘，可能已经裁切。')
        # 只清除与主体断开的生成残屑；外扩两像素保留原始半透明描边。
        keep=Image.fromarray(mask).filter(ImageFilter.MaxFilter(5))
        rgba=np.array(image);rgba[:,:,3]=(rgba[:,:,3].astype(np.uint16)*np.asarray(keep)//255).astype(np.uint8)
        cropped=Image.fromarray(rgba).crop(box)
        alpha=np.asarray(cropped)[:,:,3];ys,xs=np.where(alpha>80)
        feet=xs[ys>=ys.max()-max(2,round(alpha.shape[0]*.025))]
        anchor=(float(feet.min()+feet.max())/2,float(ys.max()))
        if horizontal_anchor=='head':
            # 迈步时只剩一只支撑脚，不能跟着那只脚左右挪整个身体。
            head_x=xs[(ys>=alpha.shape[0]*.12)&(ys<alpha.shape[0]*.48)]
            anchor=(float(np.median(head_x)),anchor[1])
        tile=cropped.resize((round(cropped.width*scale),round(cropped.height*scale)),Image.Resampling.LANCZOS)
        output=Image.new('RGBA',(size,size));x=round(size*.5-anchor[0]*scale);y=round(size*.922-anchor[1]*scale)
        if x<3 or y<3 or x+tile.width>size-3 or y+tile.height>size-3:raise ValueError('角色归一化后越界。')
        output.alpha_composite(tile,(x,y));frames.append(output)
        report.append({'source_box':box,'component_pixels':area,'foot_anchor':anchor,'placement':[x,y],'scale':scale,'horizontal_anchor':horizontal_anchor})
    return frames,report


def resize_array(array,size):
    if array.ndim==2:return np.asarray(Image.fromarray(array.astype(np.float32)).resize(size,Image.Resampling.BILINEAR)).copy()
    return np.stack([resize_array(array[:,:,i],size) for i in range(array.shape[2])],axis=2)


def sample(array,dx,dy):
    h,w=array.shape[:2];yy,xx=np.mgrid[:h,:w]
    x=np.clip(xx+dx,0,w-1);y=np.clip(yy+dy,0,h-1)
    x0=x.astype(np.int32);y0=y.astype(np.int32);x1=np.minimum(x0+1,w-1);y1=np.minimum(y0+1,h-1)
    wx=x-x0;wy=y-y0
    if array.ndim==3:wx=wx[:,:,None];wy=wy[:,:,None]
    return (array[y0,x0]*(1-wx)*(1-wy)+array[y0,x1]*wx*(1-wy)+array[y1,x0]*(1-wx)*wy+array[y1,x1]*wx*wy).astype(np.float32)


def average(array):
    padded=np.pad(array,1,mode='edge')
    return (padded[:-2,1:-1]+padded[2:,1:-1]+padded[1:-1,:-2]+padded[1:-1,2:])*0.25


def blur(array,radius=2):
    k=radius*2+1
    padded=np.pad(array,radius,mode='edge')
    total=np.pad(padded.cumsum(0).cumsum(1),((1,0),(1,0)))
    return ((total[k:,k:]-total[:-k,k:]-total[k:,:-k]+total[:-k,:-k])/(k*k)).astype(np.float32)


def optical_flow(first,second,max_displacement=20):
    """金字塔 Horn–Schunck；小幅补帧使用，非大动作视频模型。"""
    def grey(image):
        a=np.asarray(image,dtype=np.float32)/255;return (a[:,:,:3]@np.array([.299,.587,.114],dtype=np.float32))*a[:,:,3]+.5*(1-a[:,:,3])
    a=grey(first);b=grey(second);h,w=a.shape;u=v=None
    for factor in (8,4,2,1):
        size=(max(8,w//factor),max(8,h//factor));aa=resize_array(a,size);bb=resize_array(b,size)
        if u is None:u=np.zeros(aa.shape,dtype=np.float32);v=u.copy()
        else:
            old=u.shape;u=resize_array(u,size)*size[0]/old[1];v=resize_array(v,size)*size[1]/old[0]
        for _ in range(3):
            warped=sample(bb,u,v);gy,gx=np.gradient(warped);dt=warped-aa
            du=np.zeros_like(u);dv=np.zeros_like(v);den=.15**2+gx*gx+gy*gy
            for _ in range(35):
                au=average(du);av=average(dv);correction=(gx*au+gy*av+dt)/den
                du=au-gx*correction;dv=av-gy*correction
            u=blur(u+np.clip(du,-1,1),2);v=blur(v+np.clip(dv,-1,1),2)
    return np.clip(u,-max_displacement,max_displacement),np.clip(v,-max_displacement,max_displacement)


def rgba_float(image):
    a=np.asarray(image,dtype=np.float32)/255;a[:,:,:3]*=a[:,:,3:4];return a


def rgba_image(array):
    a=np.clip(array,0,1);rgb=np.divide(a[:,:,:3],a[:,:,3:4],out=np.zeros_like(a[:,:,:3]),where=a[:,:,3:4]>1e-5)
    return Image.fromarray(np.uint8(np.clip(np.concatenate((rgb,a[:,:,3:4]),axis=2),0,1)*255+.5))


def interpolate(first,second,t,forward,backward,single_cel=False):
    if t<=0:return first.copy()
    if t>=1:return second.copy()
    a=sample(rgba_float(first),-t*forward[0],-t*forward[1])
    b=sample(rgba_float(second),-(1-t)*backward[0],-(1-t)*backward[1])
    # 步态的遮挡顺序会变，单张绘图续接避免两条腿叠成半透明的四条腿。
    return rgba_image((a if t<.5 else b) if single_cel else a*(1-t)+b*t)
