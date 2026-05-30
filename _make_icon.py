"""生成应用图标 app.ico：深色圆角底 + 荧光绿字幕条（与界面主题统一）。"""
from PIL import Image, ImageDraw

S = 1024  # 高分辨率绘制，最后缩放，边缘更顺滑
BG_DARK = (30, 30, 30, 255)      # #1e1e1e
PANEL = (37, 37, 37, 255)        # #252525
GREEN = (61, 255, 133, 255)      # #3dff85
GREEN_DIM = (61, 255, 133, 170)  # 次要字幕条，稍透明

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# 外层圆角方块（带绿色描边）
m = int(S * 0.06)
radius = int(S * 0.22)
d.rounded_rectangle([m, m, S - m, S - m], radius=radius,
                    fill=PANEL, outline=GREEN, width=int(S * 0.035))

# 内部“屏幕”区域，略深，营造视频画面感
im = int(S * 0.18)
d.rounded_rectangle([im, im, S - im, S - im], radius=int(S * 0.10),
                    fill=BG_DARK)

# 字幕条：底部两行，第一行长、第二行短（通用字幕图标语义）
bar_h = int(S * 0.085)
left = int(S * 0.30)
right = int(S * 0.70)
r = bar_h // 2
y1 = int(S * 0.58)
y2 = y1 + int(bar_h * 1.7)
d.rounded_rectangle([left, y1, right, y1 + bar_h], radius=r, fill=GREEN)
d.rounded_rectangle([left, y2, int(S * 0.60), y2 + bar_h], radius=r, fill=GREEN_DIM)

# 顶部一个小播放三角，点明“视频”属性
cx, cy = S // 2, int(S * 0.40)
t = int(S * 0.075)
d.polygon([(cx - t, cy - t), (cx - t, cy + t), (cx + t, cy)], fill=GREEN)

# 多尺寸输出到 .ico
sizes = [256, 128, 64, 48, 32, 16]
icons = [img.resize((s, s), Image.LANCZOS) for s in sizes]
icons[0].save("app.ico", format="ICO",
              sizes=[(s, s) for s in sizes], append_images=icons[1:])
print("✅ app.ico 已生成")
