"""下载真实示例资源并记录来源；不下载或伪造定制动漫角色模型。"""
from pathlib import Path
import json
import io
import zipfile
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]/'src/aicompanion/assets/live2d'


def download(url,path):
    path.parent.mkdir(parents=True,exist_ok=True)
    with urlopen(Request(url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.live2d.com/'}),timeout=30) as response:
        data = response.read()
    path.write_bytes(data)
    print(path.relative_to(ROOT),len(data))


def main():
    vendor = {
        'pixi.min.js':'https://cdn.jsdelivr.net/npm/pixi.js@6.5.10/dist/browser/pixi.min.js',
        'cubism4.min.js':'https://cdn.jsdelivr.net/npm/pixi-live2d-display@0.4.0/dist/cubism4.min.js',
    }
    for name,url in vendor.items():
        download(url,ROOT/'vendor'/name)
    sdk_url = 'https://cubism.live2d.com/sdk-web/bin/CubismSdkForWeb-4-r.7.zip'
    with urlopen(Request(sdk_url,headers={'User-Agent':'Mozilla/5.0','Referer':'https://www.live2d.com/'}),timeout=30) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    for name in archive.namelist():
        target = None
        if name.endswith('/Core/live2dcubismcore.min.js'):
            target = ROOT/'vendor/live2dcubismcore.min.js'
        elif '/Samples/Resources/Haru/' in name and not name.endswith('/'):
            target = ROOT/'Haru'/name.split('/Samples/Resources/Haru/',1)[1]
        elif name.endswith(('LICENSE.md','NOTICE.md')) and name.count('/')==1:
            target = ROOT/name.rsplit('/',1)[1]
        if target:
            target = target.resolve()
            if not target.is_relative_to(ROOT.resolve()):
                raise ValueError('Unsafe SDK path')
            target.parent.mkdir(parents=True,exist_ok=True)
            target.write_bytes(archive.read(name))
            print(target.relative_to(ROOT))
    if not (ROOT/'vendor/live2dcubismcore.min.js').is_file():
        raise RuntimeError('Core missing from SDK archive')
    return 0
    base = 'https://raw.githubusercontent.com/Live2D/CubismWebSamples/develop/Samples/Resources/Haru/'
    model_path = ROOT/'Haru/Haru.model3.json'
    download(base+'Haru.model3.json',model_path)
    refs = json.loads(model_path.read_text())['FileReferences']
    files = [refs['Moc'],*refs['Textures']]
    files += [refs[k] for k in ('Physics','Pose','DisplayInfo','UserData') if k in refs]
    files += [r['File'] for r in refs.get('Expressions',[])]
    files += [r['File'] for group in refs.get('Motions',{}).values() for r in group]
    files += [r['Sound'] for group in refs.get('Motions',{}).values() for r in group if 'Sound' in r]
    for name in sorted(set(files)):
        target = (ROOT/'Haru'/name).resolve()
        if not target.is_relative_to((ROOT/'Haru').resolve()):
            raise ValueError('Unsafe asset path')
        download(base+name,target)
    download('https://raw.githubusercontent.com/Live2D/CubismWebSamples/develop/LICENSE.md',ROOT/'Haru/LICENSE.md')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
