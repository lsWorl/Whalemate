"""先构建干净目录，再更新本机程序文件，不删除现有 .env 或用户文件。"""
from pathlib import Path
import argparse
import shutil
import subprocess
import sys
import tempfile

if __package__:
    from .whale_art_files import whale_art_files
else:
    from whale_art_files import whale_art_files

ROOT=Path(__file__).resolve().parents[1]


CUBISM_CORE='_internal/aicompanion/assets/live2d/vendor/live2dcubismcore.min.js'


CHARACTER_FILES = ('character.json', 'lines.json', 'interjections.json', 'qq-chat-card.json')


def character_data_args(root, art_staging):
    """收集角色定义及内置自绘回退形象。逐文件暂存，避免打包本机图集。"""
    args = []
    for manifest in sorted((root/'src/aicompanion/characters').glob('*/character.json')):
        directory = manifest.parent
        for filename in CHARACTER_FILES:
            source = directory/filename
            if source.is_file():
                args.extend(['--add-data', f'{source}:aicompanion/characters/{directory.name}'])
    whale = root/'src/aicompanion/characters/deepseek-whale'
    for relative in whale_art_files(whale):
        target = art_staging/relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(whale/relative, target)
    # 以目录参数交给 PyInstaller，避免两百多条贴图路径超出 Windows 命令行长度。
    for folder in sorted(art_staging.iterdir()):
        args.extend(['--add-data', f'{folder}:aicompanion/characters/deepseek-whale/{folder.name}'])
    return args


def archive_whale_assets(output, backup_root):
    """更新旧的本机发布目录时，将已有图集移出待分发目录，供用户找回。"""
    whale = output/'_internal/aicompanion/characters/deepseek-whale'
    old = []
    if whale.is_dir():
        old.extend(entry for entry in whale.iterdir()
                   if entry.name in ('sprites', 'rig') or entry.name.startswith('animation-'))
    # 发布版内的安装器也可能留下源 WebP 缓存。
    cache = output/'output/aboutme-cache'
    if cache.exists():
        old.append(cache)
    if not old:
        return
    # 若用户把角色目录链接到别处，不改动链接指向的外部资源。
    for entry in old:
        if not entry.resolve().is_relative_to(output.resolve()) or entry.resolve() != entry.absolute():
            raise ValueError(f'请先将外部链接移出发布目录：{entry}')
    backup_root.mkdir(parents=True, exist_ok=True)
    backup = Path(tempfile.mkdtemp(prefix='deepseek-whale-', dir=backup_root))
    print(f'旧形象保存在 {backup}', flush=True)
    for entry in old:
        entry.rename(backup/entry.name)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    # 只给自己电脑用的构建才保留本机下载的 Core；对外发布的构建一律不带（Live2D 专有许可）。
    parser.add_argument('--keep-cubism-core',action='store_true',help='保留本机下载的 Live2D Cubism Core，仅限自用，不要对外分发')
    args=parser.parse_args()
    staging=ROOT/'build/release-staging'
    with tempfile.TemporaryDirectory(prefix='whalemate-bundled-art-') as art_staging:
        subprocess.run([sys.executable,'-m','PyInstaller','--noconfirm','--windowed','--name','AICompanion',
                        '--icon',str(ROOT/'src/aicompanion/assets/icons/aicompanion.ico'),
                        '--paths',str(ROOT/'src'),'--add-data',str(ROOT/'src/aicompanion/assets')+':aicompanion/assets',
                        *character_data_args(ROOT, Path(art_staging)),
                        '--additional-hooks-dir',str(ROOT/'tools/hooks'),
                        '--collect-submodules','webrtcvad','--hidden-import','_webrtcvad',
                        '--distpath',str(staging),'--workpath',str(ROOT/'build'),'--specpath',str(ROOT/'build'),str(ROOT/'main.py')],check=True,cwd=ROOT)
    output=ROOT/'dist/AICompanion'
    # 运行配置可能含私人密钥，不能让 PyInstaller 清空本机发布目录。
    if not args.keep_cubism_core:
        (staging/'AICompanion'/CUBISM_CORE).unlink(missing_ok=True)
    archive_whale_assets(output, ROOT/'build/local-character-backups')
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
    for name in ('LICENSE','README.md','README.en.md','.env.example'):
        shutil.copy2(ROOT/name,output/name)
    shutil.copy2(ROOT/'docs/implementation_status.md',output/'implementation_status.md')
    (output/'docs').mkdir(exist_ok=True)
    shutil.copy2(ROOT/'docs/live2d_model_sources.md',output/'docs/live2d_model_sources.md')
    shutil.copy2(ROOT/'docs/aboutme-pet.md',output/'docs/aboutme-pet.md')
    (output/'tools').mkdir(exist_ok=True)
    for name in ('install_aboutme_pet.py', 'aboutme-pet.lock.json', 'aboutme-requirements.txt'):
        shutil.copy2(ROOT/'tools'/name, output/'tools'/name)
    print(output/'AICompanion.exe')


if __name__=='__main__': main()
