"""先构建干净目录，再更新本机程序文件，不删除现有 .env 或用户文件。"""
from pathlib import Path
import argparse
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]


CUBISM_CORE='_internal/aicompanion/assets/live2d/vendor/live2dcubismcore.min.js'


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    # 只给自己电脑用的构建才保留本机下载的 Core；对外发布的构建一律不带（Live2D 专有许可）。
    parser.add_argument('--keep-cubism-core',action='store_true',help='保留本机下载的 Live2D Cubism Core，仅限自用，不要对外分发')
    args=parser.parse_args()
    staging=ROOT/'build/release-staging'
    subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--windowed','--name','AICompanion',
                    '--icon',str(ROOT/'src/aicompanion/assets/icons/aicompanion.ico'),
                    '--paths',str(ROOT/'src'),'--add-data',str(ROOT/'src/aicompanion/assets')+':aicompanion/assets',
                    '--add-data',str(ROOT/'src/aicompanion/characters')+':aicompanion/characters',
                    '--additional-hooks-dir',str(ROOT/'tools/hooks'),
                    '--collect-submodules','webrtcvad','--hidden-import','_webrtcvad',
                    '--distpath',str(staging),'--workpath',str(ROOT/'build'),'--specpath',str(ROOT/'build'),str(ROOT/'main.py')],check=True,cwd=ROOT)
    output=ROOT/'dist/AICompanion'
    # 运行配置可能含私人密钥，不能让 PyInstaller 清空本机发布目录。
    if not args.keep_cubism_core:
        (staging/'AICompanion'/CUBISM_CORE).unlink(missing_ok=True)
    shutil.copytree(staging/'AICompanion',output,dirs_exist_ok=True)
    if not args.keep_cubism_core:
        # 覆盖更新时，旧版本带进发布目录的 Core 也要清掉。
        (output/CUBISM_CORE).unlink(missing_ok=True)
    # Qt 使用 Windows 自带的不带版本号 ICU 接口；构建环境 PATH 中另一套
    # ICU 被依赖扫描误收集后会遮蔽系统 DLL，导致入口点不存在。
    for name in ('icuuc.dll','icudt78.dll'):
        (output/'_internal'/name).unlink(missing_ok=True)
    # 旧版本自带的 Haru 示例模型已经移除；覆盖更新时一并删除残留文件。
    shutil.rmtree(output/'_internal/aicompanion/assets/live2d/Haru',ignore_errors=True)
    for name in ('README.md','.env.example'):
        shutil.copy2(ROOT/name,output/name)
    shutil.copy2(ROOT/'docs/implementation_status.md',output/'implementation_status.md')
    (output/'docs').mkdir(exist_ok=True)
    shutil.copy2(ROOT/'docs/live2d_model_sources.md',output/'docs/live2d_model_sources.md')
    print(output/'AICompanion.exe')


if __name__=='__main__': main()
