"""下载 Live2D 渲染运行库（用于导入外部 Cubism 模型）并记录来源。

Live2D Cubism Core 适用 Live2D Proprietary Software License，不随本项目的仓库和打包版本分发；
需要导入外部 Live2D 模型的用户运行本脚本，从 Live2D 官网直接下载到本机。默认角色不需要它。
v2 起不再下载或自带 Haru 示例模型；不下载或伪造定制动漫角色模型。
"""
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
    print('Live2D Cubism Core 适用 Live2D Proprietary Software License：')
    print('https://www.live2d.com/eula/live2d-proprietary-software-license-agreement_cn.html')
    print('继续下载即表示你已阅读并同意该许可；下载的文件只留在本机，不会被提交或打包。')
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


if __name__ == '__main__':
    raise SystemExit(main())
