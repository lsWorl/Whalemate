"""下载固定版本的 Aboutme 大肥鱼基础动画；不读取账号，不运行远程代码。"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
from urllib.parse import quote
from urllib.request import urlopen

REVISION = 'cb205c271517336b500981a2cca050a1759bceaa'
SOURCE = 'https://github.com/hunmer/aboutme-pets'
BASE = f'https://raw.githubusercontent.com/hunmer/aboutme-pets/{REVISION}/pets/dsh/'
TARGET = Path(__file__).resolve().parents[1]/'src/aicompanion/characters/deepseek-whale/animations'
POSES = {'idle': '待机呼吸休闲', 'talking': '碎碎念-对屏碎碎念', 'thinking': '工作状态-思考冒泡',
         'working': '工作状态-忙碌点按', 'wait': '工作状态-原地踱步张望', 'celebrate': '工作状态-雀跃庆祝',
         'sleepy': '原地小憩沉眠', 'error': '工作状态-垂头叹气冒汗', 'grabbed': '被鼠标拖拽悬空反馈',
         'poked': '点击回应-傲娇生气', 'patted': '点击回应-挠痒咯咯笑', 'walking': '螃蟹走路',
         'wave': '点击回应-元气挥手'}


def fetch(relative):
    with urlopen(BASE+quote(relative, safe='/'), timeout=60) as response:
        raw = response.read(4*1024*1024+1)
    if len(raw)>4*1024*1024:
        raise ValueError('单项资源超过预定上限。')
    return raw


def main():
    TARGET.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(fetch('actions.json'))
    catalog = {a['id']: a for a in catalog['animations']}
    actions = {}; sources = []
    def one(item):
        pose, name = item
        entry = catalog[name]; remote = entry['sources']['webp']; raw = fetch(remote)
        local = pose+'.webp'; destination = TARGET/local
        if destination.exists() and destination.read_bytes()!=raw:
            raise ValueError(f'{local} 与固定源不符，拒绝覆盖。')
        destination.write_bytes(raw)
        return pose, {'image': local, 'name': name, **entry['spritesheet'], 'facing': entry.get('facing', 'left')}, {
            'file': local, 'source_path': 'pets/dsh/'+remote, 'sha256': hashlib.sha256(raw).hexdigest(), 'bytes': len(raw)}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for pose, entry, record in pool.map(one, POSES.items()):
            actions[pose]=entry; sources.append(record)
            print(pose, record['bytes'], flush=True)
    for name in ('NOTICE.txt','SOURCE-upstream.txt','LICENSE-code.txt','LICENSE-assets.txt'):
        (TARGET/name).write_bytes(fetch(name))
    manifest = {'format': 'AICompanionAnimationPack', 'version': 1, 'size': [640,360],
                'poster': 'poster.png', 'actions': actions,
                'poses': {**{p:p for p in POSES}, 'falling': 'grabbed', 'dizzy': 'error'},
                'head': [0.33,0.15,0.34,0.48]}
    (TARGET/'animations.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    (TARGET/'SOURCE.json').write_text(json.dumps({'repository': SOURCE, 'commit': REVISION,
        'upstream_repository': 'https://github.com/PC2005-cloud/dsh-pet',
        'upstream_commit': 'b78e3fcf92ac78cee569aed7cc27eeb2a8d51757',
        'license': '素材禁止商用；公开二创须署名上游；MIT 仅覆盖代码。',
        'audio_included': False, 'files': sources}, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    from PySide6.QtGui import QImage
    image = QImage(str(TARGET/'idle.webp'))
    if image.isNull() or not image.copy(0,0,640,360).save(str(TARGET/'poster.png')):
        raise ValueError('待机封面导出失败。')
    print('基础动画已下载，原始音轨未下载。', flush=True)


if __name__=='__main__':
    main()
