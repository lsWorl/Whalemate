"""发布包自检：真实渲染、消息合并和正常退出，不使用私人数据。"""
import json
from PySide6.QtCore import QTimer


def schedule_smoke_check(window, database, output):
    output.mkdir(parents=True, exist_ok=True)
    state = {'page_loaded': False, 'avatar_ready': False}
    view = window.avatar
    if hasattr(view, 'loadFinished'):
        # 外部 Cubism 模型使用 WebEngine，需要等页面和模型加载完成。
        view.loadFinished.connect(lambda ok: state.update(page_loaded=ok))
        view.bridge.loaded.connect(lambda status: state.update(avatar_ready=status in ('ready', 'animation-ready', 'illustration-ready')))
    else:
        # 鲸娘等原生立绘角色在构造时已经加载完毕。
        state.update(page_loaded=True, avatar_ready=window.pet.isVisible() and view is not None, renderer=type(view).__name__)
    QTimer.singleShot(1500, lambda: window.submit('今天有点累。'))
    QTimer.singleShot(1800, lambda: window.submit('请先听我说。'))

    def finish():
        rows = database.recent_messages(character_id=window.controller.character.id)
        state['roles'] = [row['role'] for row in rows]
        state['passed'] = state['page_loaded'] and state['avatar_ready'] and state['roles'] == ['user', 'user', 'assistant']
        window.grab().save(str(output/'window.png')); window.pet.grab().save(str(output/'pet.png'))
        (output/'report.json').write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
        window.request_exit()

    QTimer.singleShot(8000, finish)
    return state
