"""把已审的完整 PNG 等比打包为整帧序列；不切肢体、不改变姿态。"""
from __future__ import annotations
import argparse,json
from pathlib import Path
from PIL import Image

COUNTS={'pickup':2,'drag_left':4,'drag_right':4,'release':2,'fall':2,'land':3,'dizzy':4,'recover':2,'start':2,'walk':8,'run':8,'brake':2,'turn':3}
FPS={'pickup':12,'drag_left':8,'drag_right':8,'release':12,'fall':8,'land':10,'dizzy':8,'recover':8,'start':10,'walk':8,'run':12,'brake':10,'turn':10}

def compile_frames(review:Path,destination:Path):
    manifest=json.loads((review/'manifest.json').read_text(encoding='utf-8'))
    if manifest['layout_issues']:raise ValueError('先解决越界帧：'+str(manifest['layout_issues']))
    destination.mkdir(parents=True,exist_ok=True);actions={}
    config={'type':'full-frame-sequence','version':1,'canvas':[1254,1254],'anchor':[627,1156],'grab_pivot':[627,80],
            'source_scale':.2,'transition_mode':'opaque-keyframes','crossfade_ms':0,'direction_change_ms':70,
            'source_frame_size':[512,512],'atlas_cell':512,'atlas_columns':4,'run_enter_speed':110,'run_exit_speed':85,'actions':actions}
    for name,count in COUNTS.items():
        frames=sorted((f for f in manifest['frames'] if f['action']==name),key=lambda f:f['sequence_index'])
        if len(frames)!=count:raise ValueError(f'{name} 应为 {count} 帧，实际 {len(frames)}')
        item={'type':'full-frame-sequence','count':count,'fps':FPS[name],'loop':name in ('drag_left','drag_right','dizzy','walk','run'),
              'atlas':('drag_left' if name=='drag_right' else name)+'.webp','source_frames':[],
              'playback_order':[Path(f['file']).name for f in frames],
              'phase_driver':'distance' if name in ('walk','run') else 'time'}
        if name in ('walk','run'):item['stride_source']=400 if name=='walk' else 600
        if name=='dizzy':item['hold_seconds']=2.1
        if name.startswith('drag_'):item.update(phase_labels=['最大甩开','回中','小幅反摆','回中'],center_index=1)
        if name=='drag_right':item['mirror_of']='drag_left'
        actions[name]=item
        atlas=Image.new('RGBA',(4*512,((count+3)//4)*512),(0,0,0,0));gif=[]
        folder=destination/'source'/name
        if name!='drag_right':folder.mkdir(parents=True,exist_ok=True)
        for i,frame in enumerate(frames):
            image=Image.open(frame['file']).convert('RGBA')
            if image.size!=(1254,1254) or image.getchannel('A').getextrema()!=(0,255):raise ValueError('画布或 alpha 错误')
            if abs(frame['head_width_qa']['error_percent'])>2:raise ValueError('头宽误差超过2%')
            source=f'source/{name}/frame-{i+1:02}.webp'
            if name=='drag_right':source=f'source/drag_left/frame-{i+1:02}.webp'
            runtime_frame=image.resize((512,512),Image.Resampling.LANCZOS)
            if name!='drag_right':runtime_frame.save(destination/source,lossless=True,exact=True,method=6)
            item['source_frames'].append({'file':source,'review_key':Path(frame['file']).name,'sha256_png':frame['sha256'],
                'feet_y':frame['actual_feet_y'],'grounded':frame['grounded'],'head_width_qa':frame['head_width_qa']})
            atlas.paste(runtime_frame,((i%4)*512,(i//4)*512))
            bg=Image.new('RGBA',(1254,1254),'#edf1f6');bg.alpha_composite(image)
            thumb=bg.convert('RGB').resize((418,418),Image.Resampling.LANCZOS);gif.append(thumb)
        if name!='drag_right':atlas.save(destination/item['atlas'],lossless=True,exact=True,method=6)
        # 累积舍入避免 12fps GIF 因每帧 80ms 而持续加速。
        durations=[round((i+1)*100/FPS[name])*10-round(i*100/FPS[name])*10 for i in range(count)]
        gif[0].save(review/name/'preview.gif',save_all=True,append_images=gif[1:],duration=durations,loop=0,disposal=2)
    (destination/'actions.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    manifest.update(stage='whole-frame-final-review',renderer_integration=True,action_specs=actions,
        notes=['全帧 imagegen 完整角色；走跑各8帧。','头宽用固定头型的脸颊左右轮廓测量，整张等比归一，未按全身bbox缩放。','P2 尚未实现；本轮等待 Claude 最终审帧，尚未开草稿 PR。'])
    (review/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'frames':sum(COUNTS.values()),'actions':len(actions),'destination':str(destination)},ensure_ascii=False))

def rebuild_atlases(pack:Path):
    """仓库内已带等比归一后的完整源画帧，不需要生成服务即可重建运行图集。"""
    config=json.loads((pack/'actions.json').read_text(encoding='utf-8'));seen=set();cell=config['atlas_cell'];cols=config['atlas_columns']
    for spec in config['actions'].values():
        if spec['atlas'] in seen:continue
        seen.add(spec['atlas']);atlas=Image.new('RGBA',(cols*cell,((spec['count']+cols-1)//cols)*cell),(0,0,0,0))
        for i,frame in enumerate(spec['source_frames']):
            with Image.open(pack/frame['file']) as opened:image=opened.convert('RGBA').resize((cell,cell),Image.Resampling.LANCZOS)
            atlas.paste(image,((i%cols)*cell,(i//cols)*cell))
        atlas.save(pack/spec['atlas'],lossless=True,exact=True,method=6)

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('review',type=Path,nargs='?');parser.add_argument('destination',type=Path,nargs='?')
    parser.add_argument('--rebuild',type=Path,help='只用仓库里的完整源画帧重建图集')
    args=parser.parse_args()
    if args.rebuild:rebuild_atlases(args.rebuild)
    elif args.review and args.destination:compile_frames(args.review,args.destination)
    else:parser.error('请提供 review destination，或 --rebuild PACK')
