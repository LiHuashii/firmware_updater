from PIL import Image, ImageDraw

# 创建一个 256x256 的 RGBA 图像，背景透明
size = 256
img = Image.new('RGBA', (size, size), (0, 0, 0, 0))
draw = ImageDraw.Draw(img)

# 绘制一个蓝色的圆形背景（代表芯片/串口）
circle_center = (size//2, size//2)
circle_radius = size//2 - 10
draw.ellipse([circle_center[0]-circle_radius, circle_center[1]-circle_radius,
              circle_center[0]+circle_radius, circle_center[1]+circle_radius],
             fill=(0, 120, 212, 255))   # 蓝色

# 在中心绘制一个白色的“U”形（代表升级/固件）
# 绘制一个简单的向上箭头或“U”字母
# draw.rectangle([size//2 - 40, size//2 - 20, size//2 + 40, size//2 + 20], fill=(255,255,255,255))
# draw.rectangle([size//2 - 20, size//2 - 60, size//2 + 20, size//2 + 60], fill=(255,255,255,255))

# 绘制两个数据连接点（小圆点）
dot_radius = 12
draw.ellipse([size//2 - 80, size//2 - 80, size//2 - 56, size//2 - 56], fill=(255,255,255,255))
draw.ellipse([size//2 + 56, size//2 - 80, size//2 + 80, size//2 - 56], fill=(255,255,255,255))
draw.ellipse([size//2 - 80, size//2 + 56, size//2 - 56, size//2 + 80], fill=(255,255,255,255))
draw.ellipse([size//2 + 56, size//2 + 56, size//2 + 80, size//2 + 80], fill=(255,255,255,255))

# 可选：添加文字 "FW" 或 "U"
from PIL import ImageFont
try:
    font = ImageFont.truetype("arial.ttf", 80)
except:
    font = ImageFont.load_default()
draw.text((size//2 - 60, size//2 - 40), "FW", fill=(255,255,255,255), font=font)

# 保存为 ICO 文件（包含多种尺寸）
img.save('icon.ico', format='ICO', sizes=[(256,256)])
print("图标已生成: icon.ico")