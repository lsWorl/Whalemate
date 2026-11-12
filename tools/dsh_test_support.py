"""真实验收的临时目录与文本比较；不接触正式会话或读取模型凭据。"""
from contextlib import contextmanager
from pathlib import Path
import tempfile


def fixture_parent() -> Path:
    # 保持项目所在盘的文件系统语义，但避开项目说明文件的自动发现。
    project = Path(__file__).resolve().parents[1]
    return Path(project.anchor)/'.aicompanion-live'


def require_instruction_free(path: Path) -> None:
    """先检查再创建，避免模型被仓库或上级目录里的开发规则影响。"""
    for parent in (path, *path.parents):
        for name in ('AGENTS.md', 'CLAUDE.md'):
            instruction = parent/name
            if instruction.is_file():
                raise ValueError(f'验收目录不能位于项目说明文件之下：{instruction}')


@contextmanager
def temporary_fixture_root():
    parent = fixture_parent().resolve()
    require_instruction_free(parent)
    parent.mkdir(parents=True, exist_ok=True)
    # 仅清理 TemporaryDirectory 新建的这一层，不递归清理共享父目录。
    with tempfile.TemporaryDirectory(prefix='fixture-', dir=parent) as folder:
        root = Path(folder)
        require_instruction_free(root)
        yield root


@contextmanager
def temporary_dsh_home():
    """打包运行时在 session/new 加载原生模块，必须与正式运行时使用同一盘。"""
    from aicompanion.agent.sandbox import low_root
    parent = low_root().resolve()
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='dsh-live-', dir=parent) as folder:
        yield Path(folder)


def same_text_except_final_newlines(expected: str, actual: str) -> bool:
    # 只允许文件结尾的换行差异，正文、空格与内部换行必须保持一致。
    return expected.rstrip('\r\n') == actual.rstrip('\r\n')
