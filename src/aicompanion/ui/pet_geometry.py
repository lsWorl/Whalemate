"""桌宠窗口位置约束：支持负坐标副屏和移除显示器后的恢复。"""

MIN_WIDTH, MIN_HEIGHT = 120, 160


def fit_pet_geometry(geometry, screens, default_size=(340, 540)):
    if not screens:
        raise ValueError('至少需要一个可用屏幕。')
    try:
        x, y, width, height = (int(value) for value in geometry)
        if not 1<=width<=10000 or not 1<=height<=10000: raise ValueError()
    except (TypeError, ValueError, OverflowError):
        # 首次启动：放在主屏右下角，留出任务栏和边距。
        sx, sy, sw, sh = screens[0]
        width, height = default_size
        x, y = sx+sw-width-30, sy+sh-height-10
    # 优先选择窗口中心所在屏幕，否则选择距离中心最近的可用屏幕。
    cx, cy = x+width/2, y+height/2
    def distance(screen):
        sx, sy, sw, sh = screen
        dx, dy = max(sx-cx, 0, cx-(sx+sw)), max(sy-cy, 0, cy-(sy+sh))
        return dx*dx+dy*dy
    sx, sy, sw, sh = min(screens, key=distance)
    width, height = min(max(MIN_WIDTH, width), sw), min(max(MIN_HEIGHT, height), sh)
    return max(sx, min(x, sx+sw-width)), max(sy, min(y, sy+sh-height)), width, height
