import sys

# GUI 发布包没有控制台；仅自检模式写本地诊断，方便排查缺失资源。
if any(flag in sys.argv for flag in ('--smoke-test', '--pet-smoke-test')):
    from pathlib import Path
    output = Path.cwd()/('bundle-pet-smoke' if '--pet-smoke-test' in sys.argv else 'bundle-smoke')
    output.mkdir(parents=True,exist_ok=True)
    sys.stdout=sys.stderr=(output/'startup.log').open('w',encoding='utf-8',buffering=1)

from aicompanion.app import main


if __name__ == "__main__":
    raise SystemExit(main())
