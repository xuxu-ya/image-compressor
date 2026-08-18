"""v7 端到端测试：目标体积统一 KB/MB、默认智能模式、GIF 元数据防御"""
import io, os, sys, random, urllib.request, urllib.parse, json
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from PIL import Image
from compress import compress_image, _resize_only, _render_gif

def ok(cond, msg):
    print(f"  {'PASS' if cond else 'FAIL'}: {msg}")
    return cond

all_ok = True

# 1) 手动模式目标体积：5120 KB = 5 MB，JPG 应得到与 target_mb=5 一致的结果
im = Image.new('RGB', (200, 200), (200, 150, 100))
buf = io.BytesIO(); im.save(buf, 'JPEG', quality=90); raw = buf.getvalue()
r1 = compress_image(raw, target_mb=5.0)
r2 = compress_image(raw, target_mb=5120.0/1024)
print('Test1: manual target 5120KB vs 5MB')
all_ok &= ok(abs(r1['size_mb'] - r2['size_mb']) < 0.001 and r1['ok'] == r2['ok'],
             f"5120KB/1024 == 5MB ({r1['size_mb']} vs {r2['size_mb']})")

# 2) 智能模式默认：不指定尺寸且原图<=目标 -> 返回原图
im = Image.new('RGB', (100, 100), (200, 150, 100))
buf = io.BytesIO(); im.save(buf, 'JPEG', quality=90); raw = buf.getvalue()
r = compress_image(raw, target_mb=5, auto_fit=True)
print('Test2: smart default returns original')
all_ok &= ok(r['bytes'] == raw, f"identical bytes, note={r['note']}")

# 3) 指定尺寸与原图一致且原图<=目标 -> 返回原图
r = compress_image(raw, target_mb=5, width=100, height=100, auto_fit=True)
print('Test3: smart same-size returns original')
all_ok &= ok(r['bytes'] == raw, f"identical bytes when dims same, note={r['note']}")

# 4) GIF 输出色数上限说明：非 GIF 多帧输入（>256色）转 GIF 输出时，note 包含"上限"
# 用真正随机的 RGB 噪声：APNG（3B/像素）明显大于 256 色全帧 GIF（1B/像素），
# 以两者中点为目标体积 -> 智能模式确定性命中「256 色全帧」档
random.seed(7)
frames = []
for i in range(5):
    im = Image.new('RGB', (200, 200))
    px = im.load()
    for y in range(200):
        for x in range(200):
            px[x, y] = (random.randrange(256), random.randrange(256), random.randrange(256))
    frames.append(im)
# 保存为 PNG APNG（PIL 用 save_all 支持）
buf = io.BytesIO(); frames[0].save(buf, 'PNG', save_all=True, append_images=frames[1:], duration=100, loop=0); raw = buf.getvalue()
print('Test4: multi-frame PNG -> GIF color cap note')
gif256, _fc, _d = _render_gif(frames, 100, 0, 256, 1, False)
assert len(raw) > len(gif256), '测试前提不成立：原图应大于 256 色全帧 GIF'
target_mb = (len(gif256) + (len(raw) - len(gif256)) // 2) / 1024 / 1024
r = compress_image(raw, target_mb=target_mb, auto_fit=True)
print(f"  gif256={len(gif256)}B raw={len(raw)}B target={target_mb:.3f}MB")
print(f"  format_in={r['format_in']} colors_in={r['colors_in']} colors_out={r['colors_out']} note={r['note']}")
all_ok &= ok(r['format_in'] == 'PNG' and r['format_out'] == 'GIF' and r['colors_out'] == 256 and '上限' in r['note'],
             "note mentions GIF 256 color cap")

# 5) 多帧 GIF 智能压缩不崩溃
random.seed(8)
frames = [Image.new('RGB', (200, 200), (i*50, 100, 200)) for i in range(5)]
buf = io.BytesIO(); frames[0].save(buf, 'GIF', save_all=True, append_images=frames[1:], duration=100, loop=0); raw = buf.getvalue()
r = compress_image(raw, target_mb=0.5, auto_fit=True)
print('Test5: multi-frame GIF smart compression')
all_ok &= ok(r.get('ok') is not None, f"no crash, ok={r['ok']}, note={r['note']}")

# 6) _resize_only 对 GIF 不崩溃
resized, rw, rh = _resize_only(raw, 'GIF', 200, 200, 100, 100)
print('Test6: _resize_only GIF')
all_ok &= ok(rw == 100 and rh == 100 and len(resized) > 0, f"resized to {rw}x{rh}")

# 7) HTTP 批量模式返回 preview_large
im = Image.new('RGB', (300, 300), (200, 150, 100))
buf2 = io.BytesIO(); im.save(buf2, 'JPEG', quality=90); jpg_data = buf2.getvalue()

boundary = '----test_boundary_v7'
parts = []
params = json.dumps({'target': '0.5', 'auto_fit': '1', 'unit': 'mb'})
parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="params"\r\n\r\n{params}\r\n'.encode())
parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="a.gif"\r\nContent-Type: image/gif\r\n\r\n'.encode() + raw + b'\r\n')
parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="files"; filename="b.jpg"\r\nContent-Type: image/jpeg\r\n\r\n'.encode() + jpg_data + b'\r\n')
parts.append(f'--{boundary}--\r\n'.encode())
body = b''.join(parts)
req = urllib.request.Request('http://127.0.0.1:8000/batch', data=body, method='POST')
req.add_header('Content-Type', f'multipart/form-data; boundary={boundary}')
resp = urllib.request.urlopen(req)
print('Test7: HTTP batch mode')
has_preview_large = False
for line in resp.read().decode('utf-8').strip().split('\n'):
    if not line.strip(): continue
    obj = json.loads(line)
    if obj.get('entry'):
        e = obj['entry']
        if e.get('preview_large'):
            has_preview_large = True
        print(f"  {e['name']}: preview={len(e.get('preview',''))} preview_large={len(e.get('preview_large',''))}")
    elif obj.get('done'):
        print(f"  done saved_path={obj.get('saved_path')}")
all_ok &= ok(has_preview_large, "batch returns preview_large")

# 8) 智能模式递降优先级：降低帧率优先于降低色数（用户已取消「优先降色」规则）
#    给定目标夹在「256色全帧」与「256色抽帧(step=2)」之间，智能模式应优先抽帧而非降色
random.seed(11)
_f8 = []
for i in range(5):
    im = Image.new('RGB', (200, 200))
    px = im.load()
    for y in range(200):
        for x in range(200):
            px[x, y] = (random.randrange(256), random.randrange(256), random.randrange(256))
    _f8.append(im)
_buf8 = io.BytesIO(); _f8[0].save(_buf8, 'GIF', save_all=True, append_images=_f8[1:], duration=100, loop=0); _raw8 = _buf8.getvalue()
_g256f, _f, _d = _render_gif(_f8, 100, 0, 256, 1, False)   # 256色全帧
_g256s2, _f, _d = _render_gif(_f8, 100, 0, 256, 2, False)  # 256色抽帧 step=2
_t8 = (len(_g256s2) + (len(_g256f) - len(_g256s2)) // 2) / 1024 / 1024
_r8 = compress_image(_raw8, target_mb=_t8, auto_fit=True)
print('Test8: smart prefers frame drop over color reduction')
all_ok &= ok(_r8['frames_out'] < 5 and _r8['colors_out'] == 256,
             f"drop frames to {_r8['frames_out']} (from 5) keep 256 colors (note={_r8['note']})")

print()
print('ALL PASS' if all_ok else 'SOME FAILED')
sys.exit(0 if all_ok else 1)
