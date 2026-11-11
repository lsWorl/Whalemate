"""检查本地 Cubism 运行包；JSON 索引不能代替真正的 MOC3 模型。"""
import json
from pathlib import Path, PureWindowsPath


class ModelPackageError(ValueError):
    """供设置窗口显示的模型包错误。"""


# Live2D Cubism Core 适用 Live2D 专有许可，不随本项目的仓库和打包版本分发。
# 只有导入外部 Live2D 模型时才需要它；默认角色走自己的骨骼渲染和立绘，不受影响。
CUBISM_CORE = Path(__file__).resolve().parents[1]/'assets/live2d/vendor/live2dcubismcore.min.js'


def cubism_core_available(path=None):
    return Path(path or CUBISM_CORE).is_file()


def require_cubism_core(path=None):
    """没有 Core 时给出能照着做的提示，而不是让网页里的渲染静默失败。"""
    target = Path(path or CUBISM_CORE)
    if not target.is_file():
        raise ModelPackageError(
            '显示外部 Live2D 模型需要 Live2D Cubism Core。它适用 Live2D 专有许可，本项目不附带。\n'
            '源码运行：在项目目录执行 .venv\\Scripts\\python.exe tools\\fetch_avatar.py，从 Live2D 官网下载。\n'
            f'打包版本：从 Live2D 官网下载 Cubism SDK for Web，把 Core/live2dcubismcore.min.js 放到 {target}。\n'
            '下载和使用前请阅读并同意 Live2D Proprietary Software License。')
    return target


def validate_model_package(path):
    entry = Path(path).resolve()
    if not entry.name.lower().endswith('.model3.json') or not entry.is_file():
        raise ModelPackageError('请选择完整模型包中的 .model3.json，不是 PNG、PSD 或 .cmo3。')
    if entry.stat().st_size > 2*1024*1024:
        raise ModelPackageError('模型索引过大，请检查文件。')
    try:
        data = json.loads(entry.read_text(encoding='utf-8-sig'))
    except (OSError, UnicodeError, ValueError) as error:
        raise ModelPackageError('无法读取模型索引，请重新解压完整模型包。') from error
    if not isinstance(data, dict) or data.get('Version') != 3:
        raise ModelPackageError('目前支持 Cubism 3/4 的 model3.json，旧版 model.json 不能直接使用。')
    refs = data.get('FileReferences')
    if not isinstance(refs, dict):
        raise ModelPackageError('模型索引缺少 FileReferences。')

    def local_file(value):
        # 不允许索引通过 URL、绝对路径、软链接或 .. 读取模型目录外的数据。
        if not isinstance(value, str) or not value or any(char in value for char in '\\:%?#') or PureWindowsPath(value).is_absolute():
            raise ModelPackageError('模型资源必须使用包内相对路径。')
        target = (entry.parent/value).resolve()
        if not target.is_relative_to(entry.parent) or not target.is_file():
            raise ModelPackageError('模型资源缺失或不在模型包内：'+value)
        if not 0 < target.stat().st_size <= 64*1024*1024:
            raise ModelPackageError('模型资源为空或超过 64 MB：'+value)
        return target

    moc = local_file(refs.get('Moc'))
    with moc.open('rb') as stream:
        header = stream.read(8)
    if len(header)<8 or header[:4] != b'MOC3':
        raise ModelPackageError('模型缺少有效 MOC3 数据，不能把图片改名为 .moc3。')
    # 当前内置 Core 为 Cubism 4；更高版本必须由兼容的 Core 加载，不能强行尝试。
    if header[4] not in (1, 2, 3, 4):
        raise ModelPackageError('模型版本高于内置 Cubism 4 运行时，请导出兼容 Cubism 4.0 的版本。')
    textures = refs.get('Textures')
    if not isinstance(textures, list) or not textures or len(textures)>32:
        raise ModelPackageError('模型需要 1 至 32 张纹理。')
    for name in textures:
        texture = local_file(name)
        with texture.open('rb') as stream:
            if stream.read(8) != b'\x89PNG\r\n\x1a\n':
                raise ModelPackageError('Cubism 纹理必须为有效 PNG：'+name)
    for key in ('Physics', 'Pose', 'DisplayInfo', 'UserData'):
        if key in refs: local_file(refs[key])
    try:
        animations = list(refs.get('Expressions', []))
        for group in refs.get('Motions', {}).values(): animations.extend(group)
        if len(animations)>2000: raise ValueError()
        for animation in animations:
            local_file(animation['File'])
            if 'Sound' in animation: local_file(animation['Sound'])
    except (TypeError, AttributeError, KeyError, ValueError) as error:
        if isinstance(error, ModelPackageError): raise
        raise ModelPackageError('动作或表情索引格式错误。') from error
    return entry
