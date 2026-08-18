"""
图片压缩核心模块（支持 GIF 动图与 PNG/JPEG/WebP/BMP/TIFF 等常见格式）
----------------------------------------------------------------
- 默认不改变格式（输出保持输入格式），但可按要求调整尺寸 / 缩放比例
- GIF / 动图：调色板量化 + 抽帧（降低帧率，按比例放大单帧时长保持节奏）+ 缩放
- 静态图：缩放 + 颜色量化(PNG/BMP/TIFF) / 质量调节(JPEG/WebP)
- 自适应：给定目标体积(MB)，自动选择最温和方案以满足目标
- 返回完整「前后对比」元数据：尺寸、帧数、色数、体积、格式

依赖：Pillow, numpy
"""
from __future__ import annotations
import io
import numpy as np
from PIL import Image, ImageSequence

# 颜色档位（从温和到激进），抽帧仍是主力，颜色只作为兜底
COLOR_STEPS = [256, 192, 128, 96, 64, 48, 32]


# ----------------------------------------------------------------------------
# 工具函数
# ----------------------------------------------------------------------------
def _estimate_colors(img):
    """估算图像中的「显著颜色数」（按 4bit/通道量化后去重计数）。
    用于静态图的前后对比展示；GIF 则直接用调色板大小。"""
    im = img.convert("RGB")
    arr = np.asarray(im).astype(np.int32)
    arr = arr >> 4  # 降到 4bit/通道 -> 至多 4096 色
    packed = (arr[:, :, 0] << 8) | (arr[:, :, 1] << 4) | arr[:, :, 2]
    return int(np.unique(packed).size)


def _target_size(w, h, scale, tw, th):
    """根据缩放比例或指定宽高，计算目标尺寸（保持比例；只缩小不放大）。"""
    if scale and scale > 0:
        return max(1, round(w * scale)), max(1, round(h * scale))
    if tw or th:
        if tw and th:
            return tw, th
        if tw:
            return tw, max(1, round(h * tw / w))
        return max(1, round(w * th / h)), th
    return w, h


def _maybe_resize(im, opts):
    w, h = im.size
    nw, nh = _target_size(w, h, opts.get("scale", 0) or 0,
                          int(opts.get("width", 0) or 0),
                          int(opts.get("height", 0) or 0))
    if (nw, nh) == (w, h) or nw >= w or nh >= h:
        return im  # 不放大
    return im.resize((nw, nh), Image.LANCZOS)


def _save(im, fmt, **kw):
    buf = io.BytesIO()
    im.save(buf, format=fmt, **kw)
    return buf.getvalue()


def _result(ok, data, note, **kw):
    return {
        "ok": ok,
        "bytes": data,
        "size_mb": round(len(data) / 1024 / 1024, 2),
        "size_in_mb": kw.get("size_in_mb"),
        "format_in": kw.get("format_in"),
        "format_out": kw.get("format_out"),
        "width_in": kw.get("width_in"),
        "height_in": kw.get("height_in"),
        "width_out": kw.get("width_out"),
        "height_out": kw.get("height_out"),
        "frames_in": kw.get("frames_in"),
        "frames_out": kw.get("frames_out"),
        "colors_in": kw.get("colors_in"),
        "colors_out": kw.get("colors_out"),
        "duration_ms": kw.get("duration_ms"),
        "step": kw.get("step", 1),
        "note": note,
    }


# ----------------------------------------------------------------------------
# 动图（GIF 及多帧）压缩
# ----------------------------------------------------------------------------
def _render_gif(frames, dur, loop, colors, step, dither):
    """按 step 抽帧，用全局调色板量化，返回 GIF 字节、输出帧数、单帧时长。"""
    dither_mode = Image.FLOYDSTEINBERG if dither else Image.NONE
    palette = frames[0].quantize(colors=colors, method=Image.FASTOCTREE)
    keep_idx = list(range(0, len(frames), step))
    out = [frames[i].quantize(palette=palette, dither=dither_mode) for i in keep_idx]
    duration = dur * step  # 抽帧后按比例放大单帧时长，尽量保持播放节奏
    data = _save(
        out[0], "GIF", save_all=True, append_images=out[1:],
        optimize=True, disposal=2, duration=duration, loop=loop,
    )
    return data, len(keep_idx), duration


def _gif_colors_in(im):
    pal = im.palette
    if pal is not None and getattr(pal, "palette", None):
        return len(pal.palette) // 3
    if pal is not None and isinstance(getattr(pal, "colors", None), dict):
        return len(pal.colors)
    return 256


def _compress_animated(im, fmt_in, target, opts):
    frames = [f.convert("RGB") for f in ImageSequence.Iterator(im)]
    n = len(frames)
    w, h = frames[0].size
    tw, th = _target_size(w, h, opts.get("scale", 0) or 0,
                          int(opts.get("width", 0) or 0),
                          int(opts.get("height", 0) or 0))
    if (tw, th) != (w, h) and tw < w and th < h:
        frames = [f.resize((tw, th), Image.LANCZOS) for f in frames]
        ow, oh = tw, th
    else:
        ow, oh = w, h

    dur = im.info.get("duration")
    if isinstance(dur, (list, tuple)):
        dur = int(sum(dur) / len(dur)) if dur else 80
    dur = int(dur) if dur else 80
    loop = im.info.get("loop", 0)
    max_colors = int(opts.get("max_colors", 256))
    dither = bool(opts.get("dither", False))
    allow_skip = bool(opts.get("allow_frame_skip", True))
    colors_in = _gif_colors_in(im)

    # 1) 全帧尝试（颜色递减）
    for colors in [c for c in COLOR_STEPS if c <= max_colors]:
        data, fcount, duration = _render_gif(frames, dur, loop, colors, 1, dither)
        if len(data) <= target:
            return _result(True, data, "已满足目标（保留全部帧）",
                           format_in=fmt_in, format_out="GIF",
                           width_in=w, height_in=h, width_out=ow, height_out=oh,
                           frames_in=n, frames_out=fcount, colors_in=colors_in,
                           colors_out=colors, duration_ms=duration)

    if not allow_skip:
        colors = COLOR_STEPS[-1] if max_colors >= COLOR_STEPS[-1] else min(
            COLOR_STEPS, key=lambda c: abs(c - max_colors))
        data, fcount, duration = _render_gif(frames, dur, loop, colors, 1, dither)
        return _result(False, data, "已达最小（不抽帧），仍超过目标，请允许降低帧率或放宽目标",
                       format_in=fmt_in, format_out="GIF", width_in=w, height_in=h,
                       width_out=ow, height_out=oh, frames_in=n, frames_out=fcount,
                       colors_in=colors_in, colors_out=colors, duration_ms=duration)

    # 2) 抽帧：step 逐步增大
    for step in [2, 3, 4, 5, 6, 8, 10]:
        for colors in [c for c in COLOR_STEPS if c <= max_colors][:4]:
            data, fcount, duration = _render_gif(frames, dur, loop, colors, step, dither)
            if len(data) <= target:
                return _result(True, data, f"已满足目标（抽取帧率 step={step}）",
                               format_in=fmt_in, format_out="GIF",
                               width_in=w, height_in=h, width_out=ow, height_out=oh,
                               frames_in=n, frames_out=fcount, colors_in=colors_in,
                               colors_out=colors, duration_ms=duration)
        _render_gif(frames, dur, loop, COLOR_STEPS[0], step, dither)

    # 3) 兜底：最大抽帧 + 最低色
    step = 10
    colors = COLOR_STEPS[-1]
    data, fcount, duration = _render_gif(frames, dur, loop, colors, step, dither)
    return _result(False, data, f"已尽力压缩（step={step}，{colors}色），仍略超目标，请放宽目标值",
                   format_in=fmt_in, format_out="GIF", width_in=w, height_in=h,
                   width_out=ow, height_out=oh, frames_in=n, frames_out=fcount,
                   colors_in=colors_in, colors_out=colors, duration_ms=duration)


# ----------------------------------------------------------------------------
# 静态图压缩
# ----------------------------------------------------------------------------
def _compress_static(im, fmt_in, target, opts):
    w, h = im.size
    rim = _maybe_resize(im, opts)
    tw, th = rim.size
    max_colors = int(opts.get("max_colors", 256))
    quality = int(opts.get("quality", 85))
    dither = bool(opts.get("dither", False))
    colors_in = _estimate_colors(im)
    out_fmt = "JPEG" if fmt_in == "JPG" else fmt_in
    best = None

    if out_fmt == "JPEG":
        base = rim.convert("RGB") if rim.mode != "RGB" else rim
        for q in _q_ladder(quality, 10):
            buf = _save(base, "JPEG", quality=q, optimize=True)
            best = buf
            if len(buf) <= target:
                break
        out_fmt = "JPEG"

    elif out_fmt == "WEBP":
        for q in _q_ladder(quality, 20):
            buf = _save(rim, "WEBP", quality=q, method=4)
            best = buf
            if len(buf) <= target:
                break
        out_fmt = "WEBP"

    else:  # PNG / BMP / TIFF / 其他 -> 量化调色板后存 PNG；超限回退 WebP
        pim = rim.quantize(colors=max_colors, method=Image.FASTOCTREE,
                           dither=Image.FLOYDSTEINBERG if dither else Image.NONE)
        best = _save(pim, "PNG", optimize=True)
        if len(best) > target:
            for c in [128, 96, 64, 48, 32, 16]:
                b2 = _save(rim.quantize(colors=c, method=Image.FASTOCTREE), "PNG", optimize=True)
                if b2 < best:
                    best = b2
                if len(b2) <= target:
                    break
        if len(best) > target:
            for q in _q_ladder(quality, 20):
                try:
                    wbuf = _save(rim, "WEBP", quality=q, method=4)
                except Exception:
                    wbuf = None
                if wbuf and (best is None or len(wbuf) < len(best)):
                    best = wbuf
                    out_fmt = "WEBP"
                if wbuf and len(wbuf) <= target:
                    break

    colors_out = _estimate_colors(Image.open(io.BytesIO(best)))
    ok = len(best) <= target
    note = "已满足目标" if ok else f"已尽力压缩，仍略超目标，请放宽目标或降低尺寸/质量"
    if out_fmt != fmt_in and fmt_in not in ("JPEG", "JPG"):
        note += f"（格式已由 {fmt_in} 转为 {out_fmt} 以便达标）"
    return _result(ok, best, note, format_in=fmt_in, format_out=out_fmt,
                   width_in=w, height_in=h, width_out=tw, height_out=th,
                   frames_in=1, frames_out=1, colors_in=colors_in, colors_out=colors_out)


def _q_ladder(start, lo):
    """从 start 降到 lo 的质量阶梯（含 start，降序）。"""
    vals = sorted({int(start), 90, 80, 70, 60, 50, 40, 30, 20, lo}, reverse=True)
    vals = [v for v in vals if lo <= v <= max(100, int(start))]
    return vals or [lo]


# ----------------------------------------------------------------------------
# 统一入口
# ----------------------------------------------------------------------------
def compress_image(raw, *, target_mb=5.0, max_colors=256, allow_frame_skip=True,
                   dither=False, scale=0.0, width=0, height=0, quality=85):
    """压缩任意支持的图片。raw 可为 bytes 或文件对象。
    返回 dict（见 _result）。"""
    opts = dict(target_mb=target_mb, max_colors=max_colors,
               allow_frame_skip=allow_frame_skip, dither=dither,
               scale=scale, width=width, height=height, quality=quality)
    target = int(target_mb * 1024 * 1024)
    src = io.BytesIO(raw) if isinstance(raw, (bytes, bytearray)) else raw
    im = Image.open(src)
    fmt_in = (im.format or "").upper() or "UNKNOWN"
    if fmt_in == "JPG":
        fmt_in = "JPEG"
    size_in = len(raw) if isinstance(raw, (bytes, bytearray)) else None
    n_frames = getattr(im, "n_frames", 1)
    if fmt_in == "GIF" or n_frames > 1:
        r = _compress_animated(im, fmt_in, target, opts)
    else:
        r = _compress_static(im, fmt_in, target, opts)
    if size_in is not None:
        r["size_in_mb"] = round(size_in / 1024 / 1024, 2)
    return r


# 兼容旧调用
def compress_gif(src, target_mb=5.0, allow_frame_skip=True, dither=False, max_colors=256):
    raw = src.read() if hasattr(src, "read") else open(src, "rb").read()
    return compress_image(raw, target_mb=target_mb, max_colors=max_colors,
                          allow_frame_skip=allow_frame_skip, dither=dither)


if __name__ == "__main__":
    import sys
    src = sys.argv[1] if len(sys.argv) > 1 else "original.gif"
    out = sys.argv[2] if len(sys.argv) > 2 else "compressed.gif"
    target = float(sys.argv[3]) if len(sys.argv) > 3 else 5.0
    scale = float(sys.argv[4]) if len(sys.argv) > 4 else 0.0
    with open(src, "rb") as f:
        raw = f.read()
    r = compress_image(raw, target_mb=target, scale=scale)
    with open(out, "wb") as f:
        f.write(r["bytes"])
    print(f"input : {r['width_in']}x{r['height_in']}, {r['frames_in']} frames, {r['format_in']}")
    print(f"output: {r['size_mb']} MB, {r['width_out']}x{r['height_out']}, "
          f"{r['frames_out']} frames, {r['format_out']}, colors {r['colors_in']}->{r['colors_out']}")
    print(f"note  : {r['note']}")
    print(f"saved -> {out}")
