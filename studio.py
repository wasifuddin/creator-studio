"""Generation core for Risus Studio product ads.

Image ads and ad copy: Gemini API (Nano Banana 2 / Pro, Gemini Flash).
Video ads: Gemini Veo 3.1, ByteDance Seedance 2.0 (via fal.ai) or Higgsfield (Kling 2.5 Turbo).

No Streamlit here, so the module can be tested on its own and providers can be swapped
without touching the UI. Each video engine only needs an entry in VIDEO_ENGINES.
"""
from __future__ import annotations

import base64
import io
import json
import time
from dataclasses import dataclass
from typing import Callable

import requests
from google import genai
from google.genai import types
from PIL import Image, ImageDraw, ImageFont, ImageOps

# ---------------------------------------------------------------------------
# Catalogue: models and approximate AI cost per output (USD).
# ---------------------------------------------------------------------------
IMAGE_MODELS = {
    "gemini-3.1-flash-image": {"label": "Nano Banana 2", "usd": 0.07},
    "gemini-3-pro-image": {"label": "Nano Banana Pro (sharper text)", "usd": 0.14},
}
COPY_MODEL = "gemini-flash-latest"

VIDEO_ENGINES = {
    "veo-lite": {
        "label": "Google Veo 3.1 Lite", "provider": "gemini", "model": "veo-3.1-lite-generate-preview",
        "usd": 0.40, "seconds": 8, "aspects": {"9:16", "16:9"},
        "note": "Best value, sound included",
    },
    "veo-fast": {
        "label": "Google Veo 3.1 Fast", "provider": "gemini", "model": "veo-3.1-fast-generate-preview",
        "usd": 0.80, "seconds": 8, "aspects": {"9:16", "16:9"},
        "note": "Higher detail, sound included",
    },
    "seedance": {
        "label": "ByteDance Seedance 2.0", "provider": "fal", "model": "bytedance/seedance-2.0/image-to-video",
        "usd": 1.10, "seconds": 8, "aspects": {"9:16", "1:1", "16:9"},
        "note": "The engine behind TikTok's ad tools",
    },
    "higgsfield": {
        "label": "Higgsfield · Kling 2.5 Turbo Pro", "provider": "higgsfield",
        "model": "kling-video/v2.5-turbo/pro/image-to-video",
        "usd": 0.55, "seconds": 5, "aspects": {"9:16", "1:1", "16:9"},
        "note": "Realistic hands and product motion",
    },
    "veo-premium": {
        "label": "Google Veo 3.1 (premium)", "provider": "gemini", "model": "veo-3.1-generate-preview",
        "usd": 3.20, "seconds": 8, "aspects": {"9:16", "16:9"},
        "note": "Hero videos for big campaigns",
    },
}

FORMATS = {
    "9:16": "TikTok & Reels",
    "1:1": "Feed post",
    "16:9": "YouTube",
}

KEEP_PRODUCT = (
    "The product must look exactly like the reference photo: same shape, proportions, colours, materials, "
    "logo and printed text. Do not invent other brands, logos or text, and do not add any captions or words."
)

AD_STYLES = {
    "hero": {
        "name": "Hero product ad",
        "blurb": "Bold studio shot that stops the scroll",
        "image": "High-end advertising photo of this product as the hero: dramatic studio lighting, rich colour "
        "backdrop that complements the product, glossy reflections, premium magazine-ad look, generous empty "
        "space at the top for a headline.",
        "video": "Dramatic slow push-in on the product as a light sweep glides across it, subtle particles in "
        "the air, premium commercial feel. The product stays still and unchanged.",
    },
    "lifestyle": {
        "name": "Lifestyle UGC",
        "blurb": "Feels like a creator's real post",
        "image": "Authentic creator-style photo of this product in a bright real-life setting where it is "
        "naturally used, natural daylight, shallow depth of field, shot on a phone, warm and relatable.",
        "video": "Handheld phone-camera feel: the camera drifts gently towards the product in its real-life "
        "setting, natural light shifts softly. The product stays unchanged.",
    },
    "unboxing": {
        "name": "Unboxing reveal",
        "blurb": "Hands open the box, product revealed",
        "image": "Top-down unboxing photo: an open branded-neutral shipping box with tissue paper on a clean "
        "desk, hands lifting this product out, creator-style.",
        "video": "Hands lift the product out of the box and turn it towards the camera with smooth natural "
        "motion, as in a creator unboxing video.",
    },
    "seasonal": {
        "name": "Seasonal promo",
        "blurb": "Holiday or sale campaign",
        "image": "Festive seasonal promo photo of this product surrounded by warm fairy lights, pine branches "
        "and wrapped gifts, cosy golden bokeh, room at the top for an offer.",
        "video": "Soft snow drifts down and fairy lights twinkle while the camera slowly dollies in on the "
        "product. The product stays unchanged.",
    },
    "spin": {
        "name": "360 showcase",
        "blurb": "Turntable spin to show every angle",
        "image": "This product centred on a minimal round pedestal against a clean gradient backdrop, the whole "
        "product visible, hero lighting.",
        "video": "The camera orbits smoothly around the product on its pedestal, consistent lighting, every "
        "product detail stays accurate.",
    },
}

AI_LABEL = "AI-generated · Risus Studio"


class StudioError(RuntimeError):
    """A generation failed with a message that is safe to show on screen."""


@dataclass
class Media:
    data: bytes
    mime: str
    seconds: float


@dataclass
class Keys:
    gemini: str = ""
    fal: str = ""
    higgsfield: str = ""

    def has(self, provider: str) -> bool:
        return bool(getattr(self, provider))


# ---------------------------------------------------------------------------
# Inputs and outputs
# ---------------------------------------------------------------------------
def prepare_product(raw: bytes, max_side: int = 1600) -> bytes:
    """Normalise an upload: fix phone rotation, flatten transparency, cap size. Returns JPEG bytes."""
    try:
        img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    except Exception as err:
        raise StudioError("That file could not be read as an image.") from err
    if img.mode in ("RGBA", "LA", "P"):
        img = img.convert("RGBA")
        bg = Image.new("RGB", img.size, "white")
        bg.paste(img, mask=img.split()[-1])
        img = bg
    img = img.convert("RGB")
    img.thumbnail((max_side, max_side))
    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def _font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    names = ["DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial Bold.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"]
    for name in names:
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int) -> list[str]:
    lines, line = [], ""
    for word in text.split():
        trial = f"{line} {word}".strip()
        if draw.textlength(trial, font=font) <= max_width or not line:
            line = trial
        else:
            lines.append(line)
            line = word
    if line:
        lines.append(line)
    return lines


def compose_ad(image: bytes, headline: str = "", cta: str = "", accent: str = "#6d4aff") -> bytes:
    """Final exportable ad: optional headline and CTA button, plus the mandatory AI label."""
    img = Image.open(io.BytesIO(image)).convert("RGB")
    w, h = img.size
    draw = ImageDraw.Draw(img, "RGBA")
    margin = w // 18

    if headline:
        size = max(22, w // 13)
        font = _font(size)
        lines = _wrap(draw, headline.upper(), font, w - 2 * margin)[:3]
        line_h = int(size * 1.15)
        block_h = line_h * len(lines) + margin * 2
        # Top gradient so the headline reads on any background.
        for i in range(block_h):
            alpha = int(170 * (1 - i / block_h))
            draw.line([(0, i), (w, i)], fill=(0, 0, 0, alpha))
        y = margin
        for ln in lines:
            draw.text((margin, y), ln, font=font, fill="white")
            y += line_h

    if cta:
        size = max(16, w // 26)
        font = _font(size)
        tw = draw.textlength(cta, font=font)
        pad_x, pad_y = size, size // 2
        bw, bh = int(tw + 2 * pad_x), int(size + 2 * pad_y)
        x0, y0 = (w - bw) // 2, h - bh - margin * 2
        draw.rounded_rectangle((x0, y0, x0 + bw, y0 + bh), radius=bh // 2, fill=accent)
        draw.text((x0 + pad_x, y0 + pad_y - size // 8), cta, font=font, fill="white")

    # AI label (EU AI Act Art. 50, TikTok and YouTube disclosure rules) — always on.
    size = max(12, w // 50)
    font = _font(size)
    left, top, right, bottom = draw.textbbox((0, 0), AI_LABEL, font=font)
    pad = size // 2
    x, y = size, h - size - (bottom - top) - 2 * pad
    draw.rounded_rectangle((x, y, x + right - left + 2 * pad, y + bottom - top + 2 * pad), radius=pad, fill=(0, 0, 0, 150))
    draw.text((x + pad - left, y + pad - top), AI_LABEL, font=font, fill="white")

    out = io.BytesIO()
    img.save(out, "JPEG", quality=92)
    return out.getvalue()


def _gemini_error(err: Exception) -> str:
    text = str(err)
    if "API_KEY_INVALID" in text or "API key not valid" in text or "401" in text:
        return "The Gemini API key was rejected. Check GEMINI_API_KEY."
    if "RESOURCE_EXHAUSTED" in text or "429" in text:
        return "Gemini quota or rate limit reached. Wait a minute, or check billing on the Google AI Studio project."
    if "billing" in text.lower() or "FAILED_PRECONDITION" in text:
        return "This model needs billing enabled on the Google AI Studio project."
    if "PERMISSION_DENIED" in text or "403" in text:
        return "This Gemini key is not allowed to use this model."
    return f"Generation failed: {text[:300]}"


# ---------------------------------------------------------------------------
# Ad copy and images (Gemini)
# ---------------------------------------------------------------------------
def write_ad_copy(client: genai.Client, product_jpeg: bytes, name: str, offer: str, audience: str, style: str,
                  hook_hint: str = "") -> dict:
    prompt = (
        "You write short-form social ad copy for Risus creators (TikTok, Reels, YouTube Shorts). "
        f"Product name: {name or 'the product in the photo'}. "
        f"Offer or key selling point: {offer or 'none given'}. Target audience: {audience or 'general'}. "
        f"Ad style: {AD_STYLES[style]['name']}. Look at the photo to understand the product. "
        f"{f'Use or improve this hook idea: {hook_hint}. ' if hook_hint else ''}"
        "Write in a natural creator voice, never claim results, reviews or testimonials you can't verify, "
        "no medical or financial claims. Return JSON."
    )
    schema = {
        "type": "object",
        "properties": {
            "hook": {"type": "string", "description": "Spoken/on-screen hook for the first 2 seconds, max 8 words"},
            "headline": {"type": "string", "description": "Image headline, max 6 words"},
            "caption": {"type": "string", "description": "Post caption, 1-2 sentences, max 220 characters"},
            "cta": {"type": "string", "description": "Button text, max 3 words, e.g. 'Shop now'"},
            "hashtags": {"type": "array", "items": {"type": "string"}, "description": "4 hashtags without #"},
        },
        "required": ["hook", "headline", "caption", "cta", "hashtags"],
    }
    try:
        resp = client.models.generate_content(
            model=COPY_MODEL,
            contents=[types.Part.from_bytes(data=product_jpeg, mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema),
        )
        copy = json.loads(resp.text)
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err
    copy["hashtags"] = [h.lstrip("#").replace(" ", "") for h in copy.get("hashtags", [])][:4]
    return copy


# Creator profile options: tailor defaults so a new creator has fewer decisions to make.
NICHES = {
    "Beauty & skincare": {"style": "hero", "audience": "beauty and skincare fans",
                          "ideas": ["Pastel pink background", "On a marble bathroom counter", "Add water splashes",
                                    "Soft morning sunlight", "Surrounded by fresh flowers"]},
    "Fashion & accessories": {"style": "lifestyle", "audience": "style-conscious shoppers",
                              "ideas": ["City street at golden hour", "Minimal beige studio", "On a velvet surface",
                                        "Flat lay with outfit pieces", "Bold colour backdrop"]},
    "Home & living": {"style": "lifestyle", "audience": "people decorating their home",
                      "ideas": ["Cosy living room", "Scandinavian kitchen", "Warm evening lamp light",
                                "Bright airy bedroom", "Add green plants"]},
    "Tech & gadgets": {"style": "hero", "audience": "tech and gadget lovers",
                       "ideas": ["Dark background with neon edge light", "Clean white desk setup",
                                 "Futuristic reflections", "In someone's hand on the go", "Gaming setup with RGB"]},
    "Fitness & wellness": {"style": "lifestyle", "audience": "people into fitness and wellness",
                           "ideas": ["Bright gym", "Outdoor running track at sunrise", "Yoga mat at home",
                                     "Add water droplets", "Energetic bold colours"]},
    "Food & drink": {"style": "lifestyle", "audience": "foodies",
                     "ideas": ["Rustic wooden table", "Fresh ingredients around it", "Café counter",
                               "Picnic outdoors", "Steam and warm light"]},
    "Other": {"style": "hero", "audience": "", "ideas": ["Pastel background", "Outdoor setting",
                                                          "Soft morning sunlight", "Make it festive",
                                                          "Bold colour backdrop"]},
}
PLATFORMS = {
    "TikTok": ["9:16"],
    "Instagram": ["9:16", "1:1"],
    "YouTube": ["16:9", "9:16"],
    "All of them": ["9:16", "1:1", "16:9"],
}


def suggest_ideas(client: genai.Client, product_jpeg: bytes, name: str, niche: str) -> list[dict]:
    """Three concrete ad concepts a creator can pick with one tap."""
    prompt = (
        "You are a creative director for social media product ads made by influencers. Look at the product photo "
        f"(name: {name or 'unknown'}, creator niche: {niche or 'general'}) and propose 3 different ad concepts. "
        f"Each uses one style from this list: {', '.join(AD_STYLES)}. "
        "Give a short catchy title, the style, a one-sentence scene/creative direction (setting, colours, mood, no "
        "text overlays), and a scroll-stopping hook of max 8 words. Return JSON."
    )
    schema = {
        "type": "array",
        "items": {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "style": {"type": "string", "enum": list(AD_STYLES)},
                "direction": {"type": "string"},
                "hook": {"type": "string"},
            },
            "required": ["title", "style", "direction", "hook"],
        },
    }
    try:
        resp = client.models.generate_content(
            model=COPY_MODEL,
            contents=[types.Part.from_bytes(data=product_jpeg, mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=schema),
        )
        ideas = json.loads(resp.text)
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err
    return [i for i in ideas if i.get("style") in AD_STYLES][:3]


def generate_image(client: genai.Client, model: str, product_jpeg: bytes, style: str, aspect: str, direction: str = "") -> Media:
    prompt = f"{AD_STYLES[style]['image']} {f'Creative direction: {direction}. ' if direction else ''}{KEEP_PRODUCT}"
    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=model,
            contents=[types.Part.from_bytes(data=product_jpeg, mime_type="image/jpeg"), prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
                image_config=types.ImageConfig(aspect_ratio=aspect),
            ),
        )
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err

    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else None) or []:
            if part.inline_data and part.inline_data.data:
                return Media(part.inline_data.data, part.inline_data.mime_type or "image/png", time.time() - t0)
    reason = resp.candidates[0].finish_reason if resp.candidates else getattr(resp.prompt_feedback, "block_reason", None)
    raise StudioError(f"No image came back (reason: {reason}). Try another photo or style.")


def edit_image(client: genai.Client, model: str, ad_image: bytes, ad_mime: str, product_jpeg: bytes,
               instruction: str, aspect: str) -> Media:
    """Conversational edit of an existing ad image, re-anchored on the real product photo."""
    prompt = (
        f"Edit the first image (an ad photo): {instruction}. Keep the composition, camera angle and everything "
        "not mentioned the same. The second image is the real product for reference. " + KEEP_PRODUCT
    )
    t0 = time.time()
    try:
        resp = client.models.generate_content(
            model=model,
            contents=[
                types.Part.from_bytes(data=ad_image, mime_type=ad_mime),
                types.Part.from_bytes(data=product_jpeg, mime_type="image/jpeg"),
                prompt,
            ],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"], image_config=types.ImageConfig(aspect_ratio=aspect)
            ),
        )
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err
    for cand in resp.candidates or []:
        for part in (cand.content.parts if cand.content else None) or []:
            if part.inline_data and part.inline_data.data:
                return Media(part.inline_data.data, part.inline_data.mime_type or "image/png", time.time() - t0)
    raise StudioError("No edited image came back. Try describing the change differently.")


# ---------------------------------------------------------------------------
# Sound: AI voiceover (Gemini TTS) and AI music (Lyria 3)
# ---------------------------------------------------------------------------
TTS_MODEL = "gemini-3.8-flash-tts"
MUSIC_MODEL = "lyria-3-clip-preview"
VOICE_USD, MUSIC_USD = 0.01, 0.04  # approximate AI cost per clip

VOICES = {
    "Kore": "Firm and confident",
    "Puck": "Upbeat",
    "Aoede": "Breezy",
    "Leda": "Youthful",
    "Charon": "Informative",
    "Fenrir": "Excitable",
    "Zephyr": "Bright",
}

MUSIC_MOODS = {
    "hero": "Cinematic, premium, slowly building electronic music with a clean modern pulse",
    "lifestyle": "Warm, upbeat lo-fi pop with soft guitar and a relaxed groove",
    "unboxing": "Playful, light pop with plucky synths and claps, curious and fun",
    "seasonal": "Festive and cosy holiday music with bells, warm piano and a gentle beat",
    "spin": "Sleek minimal electronic music with a steady beat, modern tech feel",
}


def _interaction_audio(interaction) -> tuple[bytes, str]:
    for step in reversed(interaction.steps or []):
        for part in step.content or []:
            if getattr(part, "type", None) == "audio" and part.data:
                return base64.b64decode(part.data), part.mime_type or "audio/wav"
    raise StudioError("No audio came back. Try again.")


def synthesize_voice(client: genai.Client, script: str, voice: str) -> tuple[bytes, str]:
    if not script.strip():
        raise StudioError("Write a voiceover script first.")
    try:
        it = client.interactions.create(
            model=TTS_MODEL,
            input=[{"type": "user_input", "content": [{"type": "text", "text": script.strip()}]}],
            response_format={"type": "audio"},
            generation_config={"speech_config": [{"voice": voice}]},
        )
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err
    return _interaction_audio(it)


def generate_music(client: genai.Client, mood: str) -> tuple[bytes, str]:
    """A 30-second royalty-free instrumental clip from Lyria 3."""
    try:
        it = client.interactions.create(
            model=MUSIC_MODEL,
            input=f"{mood}. Background music for a short social media product ad. Instrumental only, no vocals.",
        )
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err
    return _interaction_audio(it)


# ---------------------------------------------------------------------------
# Video engines
# ---------------------------------------------------------------------------
Progress = Callable[[str], None]


def video_prompt(style: str, direction: str = "") -> str:
    return f"{AD_STYLES[style]['video']}{f' Mood: {direction}.' if direction else ''} No on-screen text or captions."


def generate_video(engine: str, keys: Keys, still: bytes, still_mime: str, prompt: str, aspect: str,
                   on_progress: Progress | None = None, timeout: int = 600) -> Media:
    spec = VIDEO_ENGINES[engine]
    if not keys.has(spec["provider"]):
        raise StudioError(f"{spec['label']} needs its API key. Add it in the app secrets.")
    t0 = time.time()
    run = {"gemini": _veo, "fal": _fal, "higgsfield": _higgsfield}[spec["provider"]]
    data = run(spec, keys, still, still_mime, prompt, aspect, on_progress or (lambda _: None), t0 + timeout)
    return Media(data, "video/mp4", time.time() - t0)


def _check_deadline(deadline: float) -> None:
    if time.time() > deadline:
        raise StudioError("The video took longer than 10 minutes. Please try again.")


def _veo(spec, keys, still, mime, prompt, aspect, progress, deadline) -> bytes:
    client = genai.Client(api_key=keys.gemini)
    try:
        op = client.models.generate_videos(
            model=spec["model"],
            prompt=prompt,
            image=types.Image(image_bytes=still, mime_type=mime),
            config=types.GenerateVideosConfig(
                aspect_ratio=aspect, duration_seconds=spec["seconds"], resolution="720p", number_of_videos=1
            ),
        )
        progress("Rendering with Google Veo…")
        while not op.done:
            _check_deadline(deadline)
            time.sleep(6)
            op = client.operations.get(op)
    except StudioError:
        raise
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err

    if op.error:
        msg = op.error.get("message", op.error) if isinstance(op.error, dict) else op.error
        raise StudioError(f"Veo could not make this video: {msg}")
    videos = (op.response.generated_videos if op.response else None) or []
    if not videos:
        reasons = getattr(op.response, "rai_media_filtered_reasons", None) if op.response else None
        raise StudioError(f"Veo returned no video{f': {reasons[0]}' if reasons else ''}. Try another style.")
    try:
        return client.files.download(file=videos[0].video)
    except Exception as err:
        raise StudioError(_gemini_error(err)) from err


def _http_error(provider: str, r: requests.Response) -> StudioError:
    if r.status_code in (401, 403):
        return StudioError(f"The {provider} API key was rejected.")
    if r.status_code == 402 or "balance" in r.text.lower() or "credit" in r.text.lower():
        return StudioError(f"The {provider} account is out of credit. Top it up to keep generating.")
    return StudioError(f"{provider} error {r.status_code}: {r.text[:300]}")


def _fal(spec, keys, still, mime, prompt, aspect, progress, deadline) -> bytes:
    headers = {"Authorization": f"Key {keys.fal}"}
    body = {
        "prompt": prompt,
        "image_url": f"data:{mime};base64,{base64.b64encode(still).decode()}",
        "aspect_ratio": aspect,
        "duration": str(spec["seconds"]),
        "resolution": "720p",
        "generate_audio": True,
    }
    r = requests.post(f"https://queue.fal.run/{spec['model']}", json=body, headers=headers, timeout=60)
    if not r.ok:
        raise _http_error("fal.ai", r)
    job = r.json()
    while True:
        _check_deadline(deadline)
        time.sleep(4)
        s = requests.get(job["status_url"], headers=headers, timeout=30)
        if not s.ok:
            raise _http_error("fal.ai", s)
        status = s.json().get("status")
        if status == "COMPLETED":
            break
        pos = s.json().get("queue_position")
        progress(f"In Seedance queue (#{pos + 1})…" if status == "IN_QUEUE" and pos is not None else "Rendering with Seedance…")
    res = requests.get(job["response_url"], headers=headers, timeout=60)
    if not res.ok:
        raise _http_error("fal.ai", res)
    url = (res.json().get("video") or {}).get("url")
    if not url:
        raise StudioError("Seedance finished but returned no video.")
    return requests.get(url, timeout=120).content


def _higgsfield(spec, keys, still, mime, prompt, aspect, progress, deadline) -> bytes:
    base = "https://api.higgsfield.ai"
    headers = {"Authorization": f"Key {keys.higgsfield}"}
    # 1. Upload the still to Higgsfield storage (their models take a public URL).
    up = requests.post(f"{base}/files/generate-upload-url", json={"content_type": mime}, headers=headers, timeout=30)
    if not up.ok:
        raise _http_error("Higgsfield", up)
    up = up.json()
    put = requests.put(up["upload_url"], data=still, headers=up.get("upload_headers") or {"Content-Type": mime}, timeout=120)
    if not put.ok:
        raise StudioError(f"Upload to Higgsfield failed ({put.status_code}).")
    # 2. Submit. Kling follows the aspect ratio of the input still.
    r = requests.post(
        f"{base}/{spec['model']}",
        json={"prompt": prompt, "image_url": up["public_url"], "duration": spec["seconds"]},
        headers=headers,
        timeout=60,
    )
    if not r.ok:
        raise _http_error("Higgsfield", r)
    job = r.json()
    status_url = job.get("status_url") or f"{base}/requests/{job['request_id']}/status"
    # 3. Poll.
    while True:
        _check_deadline(deadline)
        time.sleep(5)
        s = requests.get(status_url, headers=headers, timeout=30)
        if not s.ok:
            raise _http_error("Higgsfield", s)
        job = s.json()
        status = job.get("status")
        if status == "completed":
            break
        if status in ("failed", "nsfw", "canceled"):
            raise StudioError(f"Higgsfield could not make this video ({status}{': ' + job['error'] if job.get('error') else ''}).")
        progress("In Higgsfield queue…" if status == "queued" else "Rendering with Higgsfield…")
    url = (job.get("video") or {}).get("url")
    if not url:
        raise StudioError("Higgsfield finished but returned no video.")
    return requests.get(url, timeout=120).content
