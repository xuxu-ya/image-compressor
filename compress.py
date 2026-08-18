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


def _exact_colors(im, frames=1):
    """与 /probe 读取口径完全一致的颜色统计：
    P/1 模式统计各帧实际使用的调色板索引并集（最多看 50 帧），
    其他模式精确统计 RGB 去重数（超上限记 1<<24）。"""
    try:
        if im.mode in ("P", "1"):
            used = set()
            for fi in range(min(frames, 50)):
                im.seek(fi)
                used.update(im.getdata())
                if len(used) >= 256:
                    break
            return len(used)
        f = im.convert("RGB")
        cnt = f.getcolors(1 << 24)
        return len(cnt) if cnt is not None else (1 << 24)
    except Exception:
        return 0


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
        "size_mb": round(len(data) / 1024 / 1024, 3),
        "size_in_mb": kw.get("size_in_mb"),
        "format_in": kw.get("format_in"),
        "format_out": kw.get("format_out"),
        "width_in": kw.get("width_in"),
        "height_in": kw.get("height_in"),
        "width_out": kw.get("width_out"),
        "height_out": kw.get("height_out"),
        "frames_in": kw.get("frames_in"),
        "frames_out": kw.get("frames_out"),
        "fps_in": kw.get("fps_in"),
        "fps_out": kw.get("fps_out"),
        "colors_in": kw.get("colors_in"),
        "colors_out": kw.get("colors_out"),
        "duration_ms": kw.get("duration_ms"),
        "step": kw.get("step", 1),
        "note": note,
    }


def _norm_duration(d, default=80):
    """把可能是 tuple/list/str 的 duration 统一转成 int，防止 PIL 保存 GIF 时报错。"""
    if d is None:
        return default
    if isinstance(d, (list, tuple)):
        # 取第一个有效值；空序列回退 default
        for v in d:
            if v is not None:
                try:
                    return max(1, int(v)) if int(v) else default
                except Exception:
                    continue
        return default
    try:
        return max(1, int(d)) if int(d) else default
    except Exception:
        return default


def _norm_loop(loop, default=0):
    """把可能是 tuple/list 的 loop 统一转成 int。"""
    if loop is None:
        return default
    if isinstance(loop, (list, tuple)):
        for v in loop:
            if v is not None:
                try:
                    return int(v)
                except Exception:
                    continue
        return default
    try:
        return int(loop)
    except Exception:
        return default


# ----------------------------------------------------------------------------
# 动图（GIF 及多帧）压缩
# ----------------------------------------------------------------------------
def _render_gif(frames, dur, loop, colors, step, dither):
    """按 step 抽帧，用全局调色板量化，返回 GIF 字节、输出帧数、单帧时长。"""
    dither_mode = Image.FLOYDSTEINBERG if dither else Image.NONE
    palette = frames[0].quantize(colors=colors, method=Image.FASTOCTREE)
    keep_idx = list(range(0, len(frames), step))
    out = []
    for i in keep_idx:
        q = frames[i].quantize(palette=palette, dither=dither_mode)
        q.info.clear()
        out.append(q)
    duration = max(1, int(dur)) * int(step)  # 抽帧后按比例放大单帧时长，尽量保持播放节奏
    data = _save(
        out[0], "GIF", save_all=True, append_images=out[1:],
        optimize=True, disposal=2, duration=duration, loop=_norm_loop(loop),
    )
    return data, len(keep_idx), duration


def _compress_animated(im, fmt_in, target, opts, forced_step=1):
    # 先读取原始信息（必须在任何 seek/转换之前，确保与 /probe 口径一致）
    raw_dur = im.info.get("duration")
    raw_dur = _norm_duration(raw_dur)
    dur = raw_dur or 80
    n = getattr(im, "n_frames", 1)
    fps_in = round(1000.0 / raw_dur, 1) if (n > 1 and raw_dur > 0) else 0
    loop = _norm_loop(im.info.get("loop"), 0)
    # 与 /probe 读取口径一致：统计各帧实际使用的调色板索引并集
    colors_in = _exact_colors(im, n)

    frames = []
    for f in ImageSequence.Iterator(im):
        rgb = f.convert("RGB")
        # 清除原图可能存在的 tuple/list 元数据，避免 PIL 保存 GIF 时报错
        rgb.info.clear()
        frames.append(rgb)
    w, h = frames[0].size
    tw, th = _target_size(w, h, opts.get("scale", 0) or 0,
                          int(opts.get("width", 0) or 0),
                          int(opts.get("height", 0) or 0))
    if (tw, th) != (w, h) and tw < w and th < h:
        frames = [f.resize((tw, th), Image.LANCZOS) for f in frames]
        ow, oh = tw, th
    else:
        ow, oh = w, h

    max_colors = int(opts.get("max_colors", 256))
    dither = bool(opts.get("dither", False))
    allow_skip = bool(opts.get("allow_frame_skip", True))
    auto_c = bool(opts.get("auto_colors", True))
    cfix = min(256, max(2, max_colors))

    # 用户指定的目标帧数 / 帧率（分别换算为抽帧步长，取两者中更大者）
    frames_t = int(opts.get("frames", 0) or 0)
    fps = float(opts.get("fps", 0) or 0)
    user_step = 1
    if 0 < frames_t < n:
        user_step = max(user_step, -(-n // frames_t))   # ceil(n / frames_t)
    if fps > 0:
        orig_fps = 1000.0 / dur
        if fps < orig_fps:
            user_step = max(user_step, round(orig_fps / fps))
    forced_step = max(forced_step, user_step)

    def _ret(ok, data, note, colors, fcount, duration, step=1):
        fps_out = round(1000.0 / duration, 1) if duration > 0 else 0
        # GIF 输出调色板上限为 256；当输入色数>256 时，输出 256 是格式上限而非主动降色
        if colors_in > 256 and colors >= 256:
            note += "（GIF 调色板上限 256 色）"
        return _result(ok, data, note, format_in=fmt_in, format_out="GIF",
                       width_in=w, height_in=h, width_out=ow, height_out=oh,
                       frames_in=n, frames_out=fcount, colors_in=colors_in,
                       colors_out=colors, duration_ms=duration, step=step,
                       fps_in=fps_in, fps_out=fps_out)

    # 0) 指定了目标帧率：先按该帧率抽帧尝试（保持时间轴节奏）
    if forced_step > 1:
        out_fps = round(1000.0 / (dur * forced_step), 1)
        cands = [c for c in [256, 192, 128] if c <= max_colors] if auto_c else [cfix]
        for colors in cands or [cfix]:
            data, fcount, duration = _render_gif(frames, dur, loop, colors, forced_step, dither)
            if len(data) <= target:
                return _ret(True, data, f"已降至约 {out_fps} fps 并满足目标",
                            colors, fcount, duration, forced_step)
        if not allow_skip:
            colors = cfix if not auto_c else min([256, 192, 128], key=lambda c: abs(c - max_colors))
            data, fcount, duration = _render_gif(frames, dur, loop, colors, forced_step, dither)
            return _ret(False, data, f"已降至约 {out_fps} fps（不允许进一步抽帧），仍超目标，请放宽目标",
                        colors, fcount, duration, forced_step)

    # 1) 全帧尝试（不抽帧时按颜色档位递降，对应「降低色数」档位）
    if forced_step == 1:
        cands = [c for c in [256, 192, 128] if c <= max_colors] if auto_c else [cfix]
        for colors in cands or [cfix]:
            data, fcount, duration = _render_gif(frames, dur, loop, colors, 1, dither)
            if len(data) <= target:
                return _ret(True, data, "已满足目标（保留全部帧）",
                            colors, fcount, duration)

    if not allow_skip and forced_step == 1:
        if not auto_c:
            colors = cfix
        elif max_colors >= COLOR_STEPS[-1]:
            colors = COLOR_STEPS[-1]
        else:
            colors = min(COLOR_STEPS, key=lambda c: abs(c - max_colors))
        data, fcount, duration = _render_gif(frames, dur, loop, colors, 1, dither)
        return _ret(False, data, "已达最小（不抽帧），仍超过目标，请允许降低帧率或放宽目标",
                    colors, fcount, duration)

    # 2) 抽帧（allow_frame_skip=True 时按步长递降帧率）
    #    每 step 只试前两个（较低）颜色档位，控制总耗时
    if auto_c:
        step_colors = [c for c in [min(128, max_colors), min(64, max_colors), min(32, max_colors)] if c >= 32][:2]
    else:
        step_colors = [cfix]
    for step in [s for s in [2, 3, 4, 5, 6] if s > forced_step]:
        for colors in step_colors or [cfix]:
            data, fcount, duration = _render_gif(frames, dur, loop, colors, step, dither)
            if len(data) <= target:
                return _ret(True, data, f"已满足目标（抽取帧率 step={step}）",
                            colors, fcount, duration, step)

    # 3) 兜底：最大抽帧 + 最低色（极少触发，仅作保底）
    step = max(10, forced_step)
    colors = COLOR_STEPS[-1] if auto_c else cfix
    data, fcount, duration = _render_gif(frames, dur, loop, colors, step, dither)
    return _ret(False, data, f"已尽力压缩（step={step}，{colors}色），仍略超目标，请放宽目标值",
                colors, fcount, duration, step)


# ----------------------------------------------------------------------------
# 仅做尺寸缩放（保持格式、帧率、颜色等其它参数不变）
# ----------------------------------------------------------------------------
def _resize_only(raw, fmt_in, w0, h0, tw, th):
    """仅做尺寸缩放，不主动修改颜色、帧率、质量等其它参数。返回 (bytes, 实际宽, 实际高)。"""
    if (tw, th) == (w0, h0):
        return raw, w0, h0
    im = Image.open(io.BytesIO(raw))
    n = getattr(im, "n_frames", 1)
    if fmt_in == "GIF" or n > 1:
        dur = _norm_duration(im.info.get("duration"))
        loop = _norm_loop(im.info.get("loop"), 0)
        frames = []
        durs = []
        for f in ImageSequence.Iterator(im):
            rgb = f.convert("RGB").resize((tw, th), Image.LANCZOS)
            rgb.info.clear()
            frames.append(rgb)
            durs.append(_norm_duration(f.info.get("duration"), dur))
        buf = io.BytesIO()
        frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:],
                       optimize=True, disposal=2, duration=durs, loop=loop)
        return buf.getvalue(), tw, th
    rim = im.resize((tw, th), Image.LANCZOS)
    buf = io.BytesIO()
    if fmt_in == "JPEG":
        rim.convert("RGB").save(buf, "JPEG", quality=95, optimize=True)
    elif fmt_in == "WEBP":
        rim.save(buf, "WEBP", quality=95, method=4)
    else:
        try:
            rim.save(buf, fmt_in, optimize=True)
        except Exception:
            rim.save(buf, "PNG", optimize=True)
    return buf.getvalue(), tw, th


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
    auto_c = bool(opts.get("auto_colors", True))
    auto_q = bool(opts.get("auto_quality", True))
    auto_fit = bool(opts.get("auto_fit", False))
    # 与 /probe 读取口径一致：精确统计颜色数
    colors_in = _exact_colors(im, 1)
    out_fmt = "JPEG" if fmt_in == "JPG" else fmt_in
    best = None
    fixed_q = max(1, min(100, quality))

    if out_fmt == "JPEG":
        base = rim.convert("RGB") if rim.mode != "RGB" else rim
        for q in (_q_ladder(quality, 10) if auto_q else [fixed_q]):
            buf = _save(base, "JPEG", quality=q, optimize=True)
            best = buf
            if len(buf) <= target:
                break
        out_fmt = "JPEG"

    elif out_fmt == "WEBP":
        for q in (_q_ladder(quality, 20) if auto_q else [fixed_q]):
            buf = _save(rim, "WEBP", quality=q, method=4)
            best = buf
            if len(buf) <= target:
                break
        out_fmt = "WEBP"

    else:  # PNG / BMP / TIFF / 其他 -> 量化调色板后存 PNG；超限回退 WebP
        start_c = min(256, max(2, max_colors if max_colors else 256))
        pim = rim.quantize(colors=start_c, method=Image.FASTOCTREE,
                           dither=Image.FLOYDSTEINBERG if dither else Image.NONE)
        best = _save(pim, "PNG", optimize=True)
        if len(best) > target and auto_c:
            for c in [128, 96, 64, 48, 32, 16]:
                if c >= start_c:
                    continue
                b2 = _save(rim.quantize(colors=c, method=Image.FASTOCTREE), "PNG", optimize=True)
                if b2 < best:
                    best = b2
                if len(b2) <= target:
                    break
        if len(best) > target and (auto_fit or auto_q):
            for q in (_q_ladder(quality, 20) if auto_q else [fixed_q]):
                try:
                    wbuf = _save(rim, "WEBP", quality=q, method=4)
                except Exception:
                    wbuf = None
                if wbuf and (best is None or len(wbuf) < len(best)):
                    best = wbuf
                    out_fmt = "WEBP"
                if wbuf and len(wbuf) <= target:
                    break

    colors_out = _exact_colors(Image.open(io.BytesIO(best)), 1)
    ok = len(best) <= target
    note = "已满足目标" if ok else f"已尽力压缩，仍略超目标，请放宽目标或降低尺寸/质量"
    if out_fmt != fmt_in and fmt_in not in ("JPEG", "JPG"):
        note += f"（格式已由 {fmt_in} 转为 {out_fmt} 以便达标）"
    return _result(ok, best, note, format_in=fmt_in, format_out=out_fmt,
                   width_in=w, height_in=h, width_out=tw, height_out=th,
                   frames_in=1, frames_out=1, fps_in=0, fps_out=0,
                   colors_in=colors_in, colors_out=colors_out)


def _q_ladder(start, lo):
    """从 start 降到 lo 的质量阶梯（含 start，降序）。"""
    vals = sorted({int(start), 90, 80, 70, 60, 50, 40, 30, 20, lo}, reverse=True)
    vals = [v for v in vals if lo <= v <= max(100, int(start))]
    return vals or [lo]


# ----------------------------------------------------------------------------
# 统一入口
# ----------------------------------------------------------------------------
def _fit_note(r):
    if r.get("width_in") and r.get("width_out"):
        pct = round(100.0 * r["width_out"] / r["width_in"])
        r["note"] = (r.get("note") or "") + f"（已自动缩小尺寸至约 {pct}%）"


def compress_image(raw, *, target_mb=5.0, max_colors=256, allow_frame_skip=True,
                   dither=False, scale=0.0, width=0, height=0, quality=85, fps=0.0,
                   frames=0, auto_colors=True, auto_quality=True, auto_fit=False):
    """压缩任意支持的图片。raw 可为 bytes 或文件对象。
    - frames>0 时：动图按该目标帧数抽帧；fps>0 时按目标帧率抽帧（两者取更大步长）
    - auto_colors/auto_quality=False：颜色数/质量固定为用户值，不再自动降档
    - auto_fit=True：智能模式——优先缩小尺寸、降低帧率（画质/颜色保持高位），
      仍不达标才使用常规降档结果
    返回 dict（见 _result）。"""
    opts = dict(target_mb=target_mb, max_colors=max_colors,
                allow_frame_skip=allow_frame_skip, dither=dither,
                scale=scale, width=width, height=height, quality=quality,
                fps=fps, frames=frames,
                auto_colors=auto_colors, auto_quality=auto_quality, auto_fit=auto_fit)
    target = int(target_mb * 1024 * 1024)
    is_bytes = isinstance(raw, (bytes, bytearray))
    src = io.BytesIO(raw) if is_bytes else raw

    def _dispatch(image, o, forced_step=1):
        f = (image.format or "").upper() or "UNKNOWN"
        if f == "JPG":
            f = "JPEG"
        nf = getattr(image, "n_frames", 1)
        if f == "GIF" or nf > 1:
            return _compress_animated(image, f, target, o, forced_step=forced_step)
        return _compress_static(image, f, target, o)

    im = Image.open(src)
    w0, h0 = im.size
    # 预先读取原始元数据（用于智能模式「原图免检」及保证前后口径一致）
    fmt_in0 = (im.format or "").upper()
    if fmt_in0 == "JPG":
        fmt_in0 = "JPEG"
    n0 = getattr(im, "n_frames", 1)
    raw_dur0 = _norm_duration(im.info.get("duration"))
    dur0 = raw_dur0 or 80
    fps_in0 = round(1000.0 / raw_dur0, 1) if (n0 > 1 and raw_dur0 > 0) else 0
    colors_in0 = _exact_colors(im, n0)

    if auto_fit:
        # 智能模式：先用最保守参数（保帧、256色、高质量）尝试一次，
        # 不满足目标再进入递降循环
        conservative = dict(opts, auto_colors=False, max_colors=256,
                            auto_quality=False, quality=max(82, int(opts.get("quality", 85) or 85)),
                            allow_frame_skip=False, fps=0.0, frames=0)
        r = _dispatch(im, conservative)
    else:
        r = _dispatch(im, opts)

    # 智能模式：
    # 1) 原图已满足目标体积且未指定尺寸（或指定尺寸与原图一致） → 直接返回原图
    # 2) 指定了尺寸且缩放后已满足目标 → 只做等比缩放，不改帧率/颜色/质量
    # 3) 否则按 尺寸 > 帧率 > 色数 > 帧数 的优先级递降
    if auto_fit and is_bytes:
        orig_size = len(raw)
        scale = opts.get("scale", 0) or 0
        width = int(opts.get("width", 0) or 0)
        height = int(opts.get("height", 0) or 0)
        no_resize = (scale == 0 and width == 0 and height == 0)
        # 检查指定尺寸是否与原图一致
        if not no_resize:
            tw, th = _target_size(w0, h0, scale, width, height)
            if (tw, th) == (w0, h0):
                no_resize = True  # 尺寸一致，等同于不缩放
        if orig_size <= target and no_resize:
            # 不修改尺寸且原图已满足目标：直接复制原图，前后数据完全一致
            return _result(True, raw, "原图已满足目标大小，未做改动",
                           format_in=fmt_in0, format_out=fmt_in0,
                           width_in=w0, height_in=h0,
                           width_out=w0, height_out=h0,
                           frames_in=n0, frames_out=n0,
                           fps_in=fps_in0, fps_out=fps_in0,
                           colors_in=colors_in0, colors_out=colors_in0,
                           size_in_mb=round(orig_size / 1024 / 1024, 3),
                           duration_ms=dur0, step=1)
        if not no_resize:
            # 指定了尺寸（且与原图不同）：先只做等比缩放，若缩放后已满足目标则直接返回
            tw, th = _target_size(w0, h0, scale, width, height)
            resized, rw, rh = _resize_only(raw, fmt_in0, w0, h0, tw, th)
            if len(resized) <= target:
                r = _result(True, resized, "已按指定尺寸缩放，其它参数未做改动",
                            format_in=fmt_in0, format_out=fmt_in0,
                            width_in=w0, height_in=h0,
                            width_out=rw, height_out=rh,
                            frames_in=n0, frames_out=n0,
                            fps_in=fps_in0, fps_out=fps_in0,
                            colors_in=colors_in0,
                            size_in_mb=round(orig_size / 1024 / 1024, 3),
                            duration_ms=dur0, step=1)
                # 尽量读取缩放后的实际输出元数据
                try:
                    rim = Image.open(io.BytesIO(resized))
                    rn = getattr(rim, "n_frames", 1)
                    r["frames_out"] = rn
                    r["colors_out"] = _exact_colors(rim, rn)
                    if rn > 1:
                        rd = _norm_duration(rim.info.get("duration"), dur0)
                        r["fps_out"] = round(1000.0 / rd, 1) if rd > 0 else 0
                except Exception:
                    r["colors_out"] = colors_in0
                return r

    if auto_fit and not r.get("ok") and is_bytes:
        # 智能递降：外层为尺寸阶梯（优先调整尺寸），同一尺寸下按
        # 调整尺寸 > 降低帧率 > 降低色数 > 降低帧数 的优先级尝试（已取消「优先降色」规则）
        base_w = int(opts.get("width", 0) or 0)
        base_h = int(opts.get("height", 0) or 0)
        scales = (1.0, 0.92, 0.85, 0.78, 0.72, 0.65, 0.6, 0.55,
                  0.5, 0.45, 0.4, 0.35, 0.32, 0.28, 0.25, 0.22, 0.18, 0.15, 0.12)

        def _better(new, old):
            if not new:
                return False
            if old is None:
                return True
            ok_new = new.get("ok", False)
            ok_old = old.get("ok", False)
            if ok_new and not ok_old:
                return True
            if ok_new and ok_old:
                return False  # 已满足目标则保留更先找到的温和结果
            if not ok_new and not ok_old:
                return len(new.get("bytes", b"")) < len(old.get("bytes", b""))
            return False

        best = r
        for sc in scales:
            o2 = dict(opts)
            o2.update(auto_fit=False, auto_quality=False,
                      quality=max(82, int(opts.get("quality", 85) or 85)),
                      fps=0.0, frames=0)
            if base_w:
                o2["scale"] = 0
                o2["width"] = max(1, round(base_w * sc))
                o2["height"] = 0
            elif base_h:
                o2["scale"] = 0
                o2["width"] = 0
                o2["height"] = max(1, round(base_h * sc))
            else:
                o2["scale"] = sc
                o2["width"] = 0
                o2["height"] = 0

            # 1) 保帧 + 256 色 + 高质量（最温和，尽量不改动画质）
            try:
                r2 = _dispatch(Image.open(io.BytesIO(raw)),
                               dict(o2, auto_colors=False, max_colors=256, allow_frame_skip=False))
            except Exception:
                r2 = None
            if _better(r2, best):
                best = r2
            if best.get("ok"):
                break

            # 2) 降低帧率：小步长抽帧 + 延长单帧时长（保留大部分帧）
            for step in (2, 3, 4):
                try:
                    r2 = _dispatch(Image.open(io.BytesIO(raw)),
                                   dict(o2, auto_colors=False, max_colors=256, allow_frame_skip=True),
                                   forced_step=step)
                except Exception:
                    r2 = None
                if _better(r2, best):
                    best = r2
                if best.get("ok"):
                    break
            if best.get("ok"):
                break

            # 3) 降低色数（仍保帧）
            for colors in (192, 128, 96, 64, 48, 32):
                try:
                    r2 = _dispatch(Image.open(io.BytesIO(raw)),
                                   dict(o2, auto_colors=True, max_colors=colors, allow_frame_skip=False))
                except Exception:
                    r2 = None
                if _better(r2, best):
                    best = r2
                if best.get("ok"):
                    break
            if best.get("ok"):
                break

            # 4) 同时降低帧率与色数
            for step in (2, 3, 4, 5, 6):
                for colors in (192, 128, 96, 64, 48, 32):
                    try:
                        r2 = _dispatch(Image.open(io.BytesIO(raw)),
                                       dict(o2, auto_colors=True, max_colors=colors, allow_frame_skip=True),
                                       forced_step=step)
                    except Exception:
                        r2 = None
                    if _better(r2, best):
                        best = r2
                    if best.get("ok"):
                        break
                if best.get("ok"):
                    break
            if best.get("ok"):
                break

        r = best if best is not None else r
        if r.get("ok") and (r.get("width_out") or 0) < w0:
            _fit_note(r)

    if is_bytes:
        r["size_in_mb"] = round(len(raw) / 1024 / 1024, 3)
        # 统一用预读的原始元数据覆盖「前」字段，确保与 /probe 口径完全一致
        r["colors_in"] = colors_in0
        r["format_in"] = fmt_in0
        r["width_in"] = w0
        r["height_in"] = h0
        r["frames_in"] = n0
        r["fps_in"] = fps_in0
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
