"""Final-cut editor for Risus Studio: trim and join clips, on-screen hook, end card, AI label, voice and music mix.

Uses the ffmpeg binary bundled with imageio-ffmpeg, so it also runs on Streamlit Community Cloud.
Text is rendered with Pillow as transparent overlays, so the look matches the image ads exactly.
"""
from __future__ import annotations

import io
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass

import imageio_ffmpeg
from PIL import Image, ImageDraw, ImageFilter, ImageOps

from studio import AI_LABEL, _font, _wrap

FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
SIZES = {"9:16": (720, 1280), "1:1": (720, 720), "16:9": (1280, 720)}
FPS = 30
END_CARD_SECONDS = 2.5
HOOK_SECONDS = 3.5
ACCENT = "#6d4aff"


class EditorError(RuntimeError):
    """Rendering failed with a message that is safe to show on screen."""


@dataclass
class Clip:
    data: bytes
    start: float
    end: float


def _run(args: list[str], timeout: int = 300) -> None:
    proc = subprocess.run([FFMPEG, "-hide_banner", "-loglevel", "error", "-y", *args],
                          capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise EditorError(f"Video render failed: {proc.stderr.strip()[-400:]}")


def probe(data: bytes) -> tuple[float, bool]:
    """Return (duration in seconds, has an audio stream) for a media file."""
    with tempfile.NamedTemporaryFile(suffix=".bin", delete=False) as f:
        f.write(data)
        path = f.name
    try:
        err = subprocess.run([FFMPEG, "-hide_banner", "-i", path], capture_output=True, text=True, timeout=60).stderr
    finally:
        os.unlink(path)
    m = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", err)
    duration = int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else 0.0
    return duration, bool(re.search(r"Stream #.*Audio:", err))


# ---------------------------------------------------------------------------
# Overlays (Pillow → transparent full-frame PNGs)
# ---------------------------------------------------------------------------
def _hook_overlay(text: str, size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fs = max(28, min(w, h) // 13)
    font = _font(fs)
    lines = _wrap(draw, text, font, int(w * 0.84))[:3]
    y = int(h * (0.14 if h > w else 0.10))
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        draw.text(((w - tw) / 2, y), ln, font=font, fill="white", stroke_width=max(3, fs // 10), stroke_fill="black")
        y += int(fs * 1.2)
    return img


def _label_overlay(size: tuple[int, int]) -> Image.Image:
    w, h = size
    img = Image.new("RGBA", size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    fs = max(14, min(w, h) // 36)
    font = _font(fs)
    left, top, right, bottom = draw.textbbox((0, 0), AI_LABEL, font=font)
    pad = fs // 2
    # Above the bottom UI band of TikTok/Reels so the label stays visible once posted.
    x, y = fs, int(h * (0.80 if h > w else 0.90)) - (bottom - top) - 2 * pad
    draw.rounded_rectangle((x, y, x + right - left + 2 * pad, y + bottom - top + 2 * pad), radius=pad, fill=(0, 0, 0, 150))
    draw.text((x + pad - left, y + pad - top), AI_LABEL, font=font, fill="white")
    return img


def _end_card(still: bytes, headline: str, cta: str, link: str, size: tuple[int, int],
              accent: str = ACCENT) -> Image.Image:
    w, h = size
    base = ImageOps.fit(Image.open(io.BytesIO(still)).convert("RGB"), size)
    bg = base.filter(ImageFilter.GaussianBlur(w // 30))
    bg = Image.blend(bg, Image.new("RGB", size, "black"), 0.45)
    landscape = w > h
    unit = min(w, h)  # type scales with the short side so every format reads the same

    # Sharp product shot so the last frame is a packshot: centred on top, or on the left in landscape.
    if landscape:
        shot_h = int(h * 0.72)
        shot = ImageOps.fit(base, (int(shot_h * 0.85), shot_h))
        sx, sy = int(w * 0.08), (h - shot_h) // 2
        cx, col_w = int(w * 0.66), int(w * 0.52)
    else:
        shot_w = int(w * (0.56 if h > w else 0.36))
        shot = ImageOps.fit(base, (shot_w, int(shot_w * (1.15 if h > w else 1.0))))
        sx, sy = (w - shot.width) // 2, int(h * (0.14 if h > w else 0.08))
        cx, col_w = w // 2, int(w * 0.86)
    mask = Image.new("L", shot.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, *shot.size), radius=shot.width // 14, fill=255)
    bg.paste(shot, (sx, sy), mask)

    draw = ImageDraw.Draw(bg)
    head_fs, cta_fs, link_fs = max(24, unit // 16), max(18, unit // 24), max(14, unit // 34)
    head_lines = _wrap(draw, headline.upper(), _font(head_fs), col_w)[:2] if headline else []
    block = len(head_lines) * int(head_fs * 1.2) + (head_fs // 2 if head_lines else 0)
    block += (cta_fs * 3 if cta else 0) + (link_fs if link else 0)
    y = (h - block) // 2 if landscape else sy + shot.height + int(h * 0.04)

    font = _font(head_fs)
    for ln in head_lines:
        draw.text((cx - draw.textlength(ln, font=font) / 2, y), ln, font=font, fill="white")
        y += int(head_fs * 1.2)
    if head_lines:
        y += head_fs // 2
    if cta:
        font = _font(cta_fs)
        bw, bh = int(draw.textlength(cta, font=font) + 2 * cta_fs), cta_fs * 2
        x0 = cx - bw // 2
        draw.rounded_rectangle((x0, y, x0 + bw, y + bh), radius=bh // 2, fill=accent)
        draw.text((x0 + cta_fs, y + cta_fs // 2 - cta_fs // 8), cta, font=font, fill="white")
        y += bh + cta_fs
    if link:
        font = _font(link_fs, bold=False)
        draw.text((cx - draw.textlength(link, font=font) / 2, y), link, font=font, fill=(235, 235, 245))
    return bg


# ---------------------------------------------------------------------------
# Render
# ---------------------------------------------------------------------------
def render_final(
    clips: list[Clip],
    aspect: str,
    hook: str = "",
    end_card: dict | None = None,  # {"still", "headline", "cta", "link", optional "accent"}
    voice: bytes | None = None,
    music: bytes | None = None,
    vol_original: float = 1.0,
    vol_voice: float = 1.0,
    vol_music: float = 0.35,
    silent: bool = False,
) -> bytes:
    if not clips:
        raise EditorError("Pick at least one video clip.")
    w, h = SIZES[aspect]
    norm = (f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,fps={FPS},format=yuv420p")
    enc = ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
           "-c:a", "aac", "-ar", "44100", "-ac", "2"]

    with tempfile.TemporaryDirectory() as tmp:
        parts, total = [], 0.0
        # 1. Normalise every clip to the same size, frame rate and stereo audio track.
        for i, clip in enumerate(clips):
            src = os.path.join(tmp, f"src{i}.mp4")
            with open(src, "wb") as f:
                f.write(clip.data)
            _, has_audio = probe(clip.data)
            length = max(0.5, clip.end - clip.start)
            out = os.path.join(tmp, f"part{i}.mp4")
            audio = "[0:a]aresample=44100,aformat=channel_layouts=stereo[a]" if has_audio else "[1:a]anull[a]"
            _run(["-ss", f"{clip.start:.2f}", "-t", f"{length:.2f}", "-i", src,
                  "-f", "lavfi", "-t", f"{length:.2f}", "-i", "anullsrc=r=44100:cl=stereo",
                  "-filter_complex", f"[0:v]{norm}[v];{audio}", "-map", "[v]", "-map", "[a]",
                  "-t", f"{length:.2f}", *enc, out])
            parts.append(out)
            total += length

        # 2. End card: packshot, headline, CTA and tracked link.
        if end_card:
            card = os.path.join(tmp, "card.png")
            _end_card(end_card["still"], end_card.get("headline", ""), end_card.get("cta", ""),
                      end_card.get("link", ""), (w, h), end_card.get("accent", ACCENT)).save(card)
            out = os.path.join(tmp, "card.mp4")
            _run(["-loop", "1", "-t", str(END_CARD_SECONDS), "-i", card,
                  "-f", "lavfi", "-t", str(END_CARD_SECONDS), "-i", "anullsrc=r=44100:cl=stereo",
                  "-filter_complex", f"[0:v]{norm},fade=t=in:st=0:d=0.35[v]", "-map", "[v]", "-map", "1:a",
                  "-t", str(END_CARD_SECONDS), *enc, out])
            parts.append(out)
            total += END_CARD_SECONDS

        # 3. Join, overlay the hook and AI label, and mix audio in one pass.
        inputs: list[str] = []
        for p in parts:
            inputs += ["-i", p]
        label_png = os.path.join(tmp, "label.png")
        _label_overlay((w, h)).save(label_png)
        inputs += ["-i", label_png]
        label_idx = len(parts)
        hook_idx = None
        if hook:
            hook_png = os.path.join(tmp, "hook.png")
            _hook_overlay(hook, (w, h)).save(hook_png)
            inputs += ["-i", hook_png]
            hook_idx = label_idx + 1
        next_idx = label_idx + 1 + (1 if hook else 0)

        voice_idx = music_idx = None
        if voice and not silent:
            vp = os.path.join(tmp, "voice.bin")
            with open(vp, "wb") as f:
                f.write(voice)
            inputs += ["-i", vp]
            voice_idx, next_idx = next_idx, next_idx + 1
        if music and not silent:
            mp = os.path.join(tmp, "music.bin")
            with open(mp, "wb") as f:
                f.write(music)
            inputs += ["-stream_loop", "-1", "-i", mp]
            music_idx = next_idx

        n = len(parts)
        if silent:
            chain = "".join(f"[{i}:v]" for i in range(n)) + f"concat=n={n}:v=1:a=0[cv]"
        else:
            chain = "".join(f"[{i}:v][{i}:a]" for i in range(n)) + f"concat=n={n}:v=1:a=1[cv][ca]"
        video = f"[cv][{label_idx}:v]overlay=0:0[v1]"
        if hook_idx is not None:
            video += f";[v1][{hook_idx}:v]overlay=0:0:enable='between(t,0,{HOOK_SECONDS})'[vout]"
        else:
            video += ";[v1]null[vout]"
        graph = [chain, video]

        if not silent:
            mix = [f"[ca]volume={vol_original:.2f}[a0]"]
            labels = ["[a0]"]
            if voice_idx is not None:
                mix.append(f"[{voice_idx}:a]aresample=44100,aformat=channel_layouts=stereo,adelay=300|300,"
                           f"volume={vol_voice:.2f}[a1]")
                labels.append("[a1]")
            if music_idx is not None:
                fade_at = max(0.0, total - 1.5)
                mix.append(f"[{music_idx}:a]aresample=44100,aformat=channel_layouts=stereo,atrim=0:{total:.2f},"
                           f"afade=t=in:d=0.4,afade=t=out:st={fade_at:.2f}:d=1.5,volume={vol_music:.2f}[a2]")
                labels.append("[a2]")
            mix.append(f"{''.join(labels)}amix=inputs={len(labels)}:duration=first:normalize=0,"
                       f"alimiter=limit=0.95[aout]")
            graph += mix

        final = os.path.join(tmp, "final.mp4")
        maps = ["-map", "[vout]"] + ([] if silent else ["-map", "[aout]"])
        _run([*inputs, "-filter_complex", ";".join(graph), *maps, "-t", f"{total:.2f}",
              *enc, *(["-an"] if silent else []), "-movflags", "+faststart", final], timeout=600)
        with open(final, "rb") as f:
            return f.read()
