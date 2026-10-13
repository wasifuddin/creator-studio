"""Risus Studio — product ad generator for Risus creators (Streamlit).

Flow: ① Create → ② Edit → ③ Add sound & finish → 📈 My results.
Run locally:  streamlit run app.py
Secrets (or env vars): GEMINI_API_KEY, optional FAL_KEY, HIGGSFIELD_API_KEY and APP_PASSWORD (access code).
"""
from __future__ import annotations

import hmac
import io
import os
import random
import re
import time
import uuid
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import altair as alt
import pandas as pd
import streamlit as st
from google import genai

import editor
import store
import studio
from studio import AD_STYLES, FORMATS, IMAGE_MODELS, NICHES, PLATFORMS, VIDEO_ENGINES

st.set_page_config(page_title="Risus Studio", page_icon="🎬", layout="wide")

ASSETS = Path(__file__).parent / "assets"
PROVIDER_KEY_NAME = {"gemini": "GEMINI_API_KEY", "fal": "FAL_KEY", "higgsfield": "HIGGSFIELD_API_KEY"}
NAV = ["① Create", "② Edit", "③ Add sound & finish", "📈 My results"]
DEFAULT_ACCENT = "#6d4aff"
SAMPLE = {"name": "Glow Serum 30ml", "offer": "20% off this week"}
MOTION_IDEAS = ["Slower, smoother camera", "Orbit around the product", "Add sparkle and light flares",
                "A hand picks it up", "End close on the label"]
STUDIO_ID = "demo"  # no login for the demo: one studio, editable in ⚙️ Settings
DEFAULT_PROFILE = {"name": "Maya", "handle": "maya", "niche": "Beauty & skincare", "platform": "Instagram"}

TIPS = [
    "Tip: the first 2 seconds decide if people keep watching, so lead with your hook.",
    "Tip: your own voice builds more trust than an AI voice. Try recording it in step ③.",
    "Tip: post the 9:16 video to TikTok and Reels, and the square image to your feed.",
    "Tip: every ad carries your tracked Risus link, so you get credit for everyone you bring.",
    "Tip: you can edit any image just by describing the change in step ②.",
    "Tip: always switch on the platform's AI label when posting. It protects your account.",
]
CHECKLIST = [
    "**Paste the caption.** It already has your tracked Risus link, #ad and #AIgenerated.",
    "**Switch on the AI label.** TikTok: *AI-generated content*. Instagram: *AI info*. "
    "YouTube: *Altered or synthetic content → Yes*.",
    "**Mark it as an ad** (paid partnership) if you were paid or gifted the product.",
    "**Add your Risus link** to your bio or a link sticker as well.",
    "**Check the product** looks exactly like the real one before you post.",
]
FAQ = {
    "Why is there an 'AI-generated' label?": "EU law and TikTok, Instagram and YouTube rules require AI content to be "
                                             "labelled. Keeping the label protects your account and your "
                                             "audience's trust.",
    "Will the AI change my product?": "It always starts from your real photo and is told to keep the shape, colours, "
                                      "logo and text. Still, check every ad before you post it.",
    "Can I use my own voice or music?": "Yes, in step ③. Record your voice right in the browser, or upload your own "
                                        "audio. Only upload music you have the rights to use in ads.",
    "What is my Risus link for?": "Every ad and caption carries it, so Risus can see who joined because of you.",
    "Where are my ads saved?": "In your Risus Studio library. They're still here when you come back.",
}

st.markdown(
    """
    <style>
      .block-container { padding-top: 2rem; max-width: 1400px; }
      .ai-pill { display:inline-block; background:#17151f; color:#fff; font-size:11px; font-weight:600;
                 padding:2px 8px; border-radius:6px; margin-right:6px; }
      .meta { color:#6b6779; font-size:13px; }
      .hook { font-size:20px; font-weight:700; line-height:1.3; margin:4px 0 8px; }
      .step-num { display:inline-block; width:26px; height:26px; border-radius:50%; background:#ece7ff;
                  color:#6d4aff; font-weight:700; text-align:center; line-height:26px; margin-right:8px; }
      div[data-testid="stMetricValue"] { font-size: 26px; }
      /* Style cards stay in one row and scroll sideways on phones instead of stacking. */
      .st-key-styles [data-testid="stHorizontalBlock"] { flex-wrap: nowrap !important; overflow-x: auto; }
      .st-key-styles [data-testid="stColumn"] { min-width: 128px !important; }
    </style>
    """,
    unsafe_allow_html=True,
)


def secret(name: str) -> str:
    try:
        value = st.secrets.get(name, "")
    except Exception:  # no secrets.toml present
        value = ""
    return str(value or os.environ.get(name, "")).strip()


KEYS = studio.Keys(gemini=secret("GEMINI_API_KEY"), fal=secret("FAL_KEY"), higgsfield=secret("HIGGSFIELD_API_KEY"))
ACCESS_CODE = secret("APP_PASSWORD")
ss = st.session_state


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "product").lower()).strip("-")[:40] or "product"


# ===========================================================================
# Access and creator profile
# ===========================================================================
def gate() -> None:
    """Optional access code so a public deployment can't run up the AI bill (set APP_PASSWORD)."""
    if not ACCESS_CODE or ss.get("authed"):
        return
    st.title("🎬 Risus Studio")
    with st.form("gate"):
        code = st.text_input("Access code", type="password")
        if st.form_submit_button("Enter", type="primary"):
            if hmac.compare_digest(code.strip(), ACCESS_CODE):
                ss.authed = True
                st.rerun()
            st.error("That code isn't right.")
    st.stop()


def start_session() -> None:
    data = store.load(STUDIO_ID) or {}
    ss.uid = STUDIO_ID
    ss.profile = {**DEFAULT_PROFILE, **data.get("profile", {})}
    ss.campaigns = data.get("campaigns", [])
    ss.active = ss.campaigns[0]["id"] if ss.campaigns else None
    ss.nav = NAV[0]
    ss.p_formats = list(PLATFORMS[ss.profile.get("platform", "Instagram")])
    ss.style_pick = NICHES[ss.profile.get("niche", "Other")]["style"]
    ss.voice_pref = ss.profile.get("voice", "Kore")
    ss.accent = ss.profile.get("accent", DEFAULT_ACCENT)


gate()
if "uid" not in ss:
    start_session()

if not KEYS.gemini:
    st.error("**GEMINI_API_KEY is missing.** Add it to `.streamlit/secrets.toml` locally, or to *Settings → Secrets* "
             "on Streamlit Community Cloud, then reload.")
    st.stop()

client = genai.Client(api_key=KEYS.gemini)
PROFILE = ss.profile
HANDLE = PROFILE.get("handle", "creator")
NICHE = NICHES.get(PROFILE.get("niche", "Other"), NICHES["Other"])
ss.setdefault("image_model", next(iter(IMAGE_MODELS)))
ss.setdefault("video_engine", next(iter(VIDEO_ENGINES)))


# ===========================================================================
# Helpers
# ===========================================================================
def new_id() -> str:
    return uuid.uuid4().hex[:8]


def tracked_link(product: str, style: str, aspect: str) -> str:
    query = urlencode({"p": slug(product), "s": style, "f": aspect.replace(":", "x")})
    return f"https://risus.app/r/{HANDLE}?{query}"


def engine_label(key: str) -> str:
    spec = VIDEO_ENGINES[key]
    return f"{'' if KEYS.has(spec['provider']) else '🔒 '}{spec['label']}"


def goto(step: str, **state) -> None:
    """Button callback: switch step (runs before widgets render, so it may set widget keys)."""
    ss.nav = step
    ss.update(state)


def run_parallel(jobs: dict, status, label: str, tip=None) -> dict:
    """Run {key: callable} in threads while the status box ticks. Returns {key: result | StudioError}."""
    results, t0 = {}, time.time()
    with ThreadPoolExecutor(max_workers=max(1, len(jobs))) as pool:
        futures = {k: pool.submit(fn) for k, fn in jobs.items()}
        while not all(f.done() for f in futures.values()):
            done, secs = sum(f.done() for f in futures.values()), int(time.time() - t0)
            status.update(label=f"{label} · {done}/{len(futures)} done · {secs}s")
            if tip is not None:
                tip.caption(TIPS[(secs // 6) % len(TIPS)])
            time.sleep(1)
    for k, f in futures.items():
        try:
            results[k] = f.result()
        except studio.StudioError as err:
            results[k] = err
        except Exception as err:  # unexpected; show it rather than crash the page
            results[k] = studio.StudioError(f"Unexpected error: {err}")
    return results


def campaign_by_id(cid: str | None) -> dict | None:
    return next((c for c in ss.campaigns if c["id"] == cid), None)


def pick_campaign(label: str = "Ad set") -> dict | None:
    if not ss.campaigns:
        return None
    ids = [c["id"] for c in ss.campaigns]
    if ss.active not in ids:
        ss.active = ids[0]
    if len(ids) > 1:
        # No widget key: the index follows ss.active, so "Edit" buttons can switch ad sets.
        ss.active = st.selectbox(
            label, ids, index=ids.index(ss.active),
            format_func=lambda i: (f"{campaign_by_id(i)['product']} · {AD_STYLES[campaign_by_id(i)['style']]['name']}"
                                   f" · {campaign_by_id(i).get('created', '')}"),
        )
    return campaign_by_id(ss.active)


def images_of(camp: dict) -> list[dict]:
    return [i for i in camp["items"] if i["kind"] == "image"]


def videos_of(camp: dict, aspect: str | None = None) -> list[dict]:
    return [i for i in camp["items"] if i["kind"] == "video" and (aspect is None or i["aspect"] == aspect)]


def item_title(it: dict) -> str:
    base = f"{FORMATS[it['aspect']]} · {it['aspect']}"
    if it["kind"] == "image":
        return f"{base} · v{it['v_idx'] + 1}"
    return f"{base} · {it['label']}"


def compose(camp: dict, it: dict) -> None:
    copy = camp["copy"] or {}
    on = camp["overlay"]
    it["data"] = studio.compose_ad(it["raw"], copy.get("headline", "") if on else "",
                                   copy.get("cta", "") if on else "", accent=ss.accent)


def usable_engines(aspect: str) -> list[str]:
    return [k for k, v in VIDEO_ENGINES.items() if KEYS.has(v["provider"]) and aspect in v["aspects"]]


def make_video(camp: dict, src_raw: bytes, src_mime: str, aspect: str, engine: str, prompt: str, label: str) -> bool:
    eng = VIDEO_ENGINES[engine]
    with st.status("Turning it into a video…", expanded=True) as status:
        tip = st.empty()
        res = run_parallel({"v": lambda: studio.generate_video(engine, KEYS, src_raw, src_mime, prompt, aspect)},
                           status, "Filming your video (usually under a minute)", tip)["v"]
        tip.empty()
        if isinstance(res, studio.StudioError):
            status.update(label=str(res), state="error")
            st.error(str(res))
            return False
        camp["items"].append(video_item(res, aspect, eng, engine, src_raw, src_mime, prompt, label))
        status.update(label=f"Video ready in {res.seconds:.0f}s", state="complete", expanded=False)
    return True


def video_item(res, aspect, eng, engine, src_raw, src_mime, prompt, label) -> dict:
    duration, _ = editor.probe(res.data)
    return {"id": new_id(), "kind": "video", "aspect": aspect, "data": res.data, "mime": res.mime,
            "seconds": res.seconds, "model": eng["label"], "engine": engine, "src_raw": src_raw,
            "src_mime": src_mime, "prompt": prompt, "label": label, "duration": duration or float(eng["seconds"])}


def caption_for(camp: dict, aspect: str = "9:16") -> str:
    link = tracked_link(camp["product"], camp["style"], aspect)
    cp = camp["copy"]
    if not cp:
        return f"Shop it on Risus 👉 {link}\n\n#ad #AIgenerated"
    tags = " ".join(f"#{h}" for h in cp.get("hashtags", []))
    return f"{cp['caption']}\n\nShop it on Risus 👉 {link}\n\n#ad #AIgenerated {tags}".strip()


def share_kit(camp: dict) -> bytes:
    """Everything a creator needs to post, in one zip."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as z:
        for it in camp["items"]:
            ext = "jpg" if it["kind"] == "image" else "mp4"
            z.writestr(f"{slug(FORMATS[it['aspect']])}-{it['kind']}-{it['id']}.{ext}", it["data"])
        for aspect, data in camp["finals"].items():
            z.writestr(f"FINAL-{slug(FORMATS[aspect])}.mp4", data)
        z.writestr("caption.txt", caption_for(camp))
        z.writestr("before-you-post.txt", "\n".join(f"[ ] {c.replace('*', '')}" for c in CHECKLIST))
    return buf.getvalue()


def persist() -> None:
    """Save the studio when something changed (cheap fingerprint, so most reruns skip the write)."""
    fp = hash((
        repr(sorted(ss.profile.items())),
        tuple((c["id"], len(c["items"]), sum(len(i.get("versions", [])) * 31 + i.get("v_idx", 0) for i in c["items"]),
               tuple(sorted((a, len(d)) for a, d in c["finals"].items())), repr(c["copy"]), c["overlay"],
               len((c.get("ai_voice") or {}).get("data", b"")), len((c.get("ai_music") or {}).get("data", b"")))
              for c in ss.campaigns),
    ))
    if ss.get("_saved_fp") != fp:
        store.save(STUDIO_ID, {"profile": ss.profile, "campaigns": ss.campaigns})
        ss._saved_fp = fp


def section(num: str, title: str) -> None:
    st.markdown(f"<h4><span class='step-num'>{num}</span>{title}</h4>", unsafe_allow_html=True)


# ===========================================================================
# Sidebar: profile, settings, help
# ===========================================================================
def reset_studio() -> None:
    """Delete every ad (handy before a demo). The profile is kept."""
    store.save(STUDIO_ID, {"profile": ss.profile})
    for k in list(ss.keys()):
        if k != "authed":
            del ss[k]


with st.sidebar:
    st.markdown("## 🎬 Risus Studio")
    with st.container(border=True):
        st.markdown(f"**👤 {PROFILE.get('name') or HANDLE}**")
        st.caption(f"@{HANDLE} · {PROFILE.get('niche', '')}")
        st.caption("Your Risus link")
        st.code(f"risus.app/r/{HANDLE}", language=None)

    with st.expander("⚙️ Settings"):
        n1, n2 = st.columns(2)
        name = n1.text_input("Your name", value=PROFILE.get("name", ""))
        handle = n2.text_input("Risus handle", value=HANDLE, help="Used in your tracked links")
        if (name.strip(), slug(handle.lstrip("@"))) != (PROFILE.get("name"), HANDLE) and name.strip() and handle.strip():
            ss.profile.update(name=name.strip(), handle=slug(handle.lstrip("@")))
            st.rerun()
        niche = st.selectbox("What do you post about?", list(NICHES),
                             index=list(NICHES).index(PROFILE["niche"]) if PROFILE.get("niche") in NICHES else 0)
        platform = st.selectbox("Where do you post most?", list(PLATFORMS),
                                index=list(PLATFORMS).index(PROFILE.get("platform", "Instagram")))
        if (niche, platform) != (PROFILE.get("niche"), PROFILE.get("platform")):
            ss.profile.update(niche=niche, platform=platform)
            st.rerun()
        accent = st.color_picker("Brand colour (buttons and end card)", value=ss.accent)
        if accent != ss.accent:
            ss.accent = ss.profile["accent"] = accent
            for c in ss.campaigns:
                for it in images_of(c):
                    compose(c, it)
        ss.profile["voice"] = st.selectbox("Default AI voice", list(studio.VOICES), key="voice_pref",
                                           format_func=lambda k: f"{k} · {studio.VOICES[k]}")
        st.markdown("**Advanced: AI models**")
        st.selectbox("Image model", list(IMAGE_MODELS), key="image_model",
                     format_func=lambda k: IMAGE_MODELS[k]["label"])
        st.selectbox("Video engine", list(VIDEO_ENGINES), key="video_engine", format_func=engine_label)
        spec_ = VIDEO_ENGINES[ss.video_engine]
        st.caption(f"{spec_['note']} · {spec_['seconds']}s clips")
        if not KEYS.has(spec_["provider"]):
            st.warning(f"Add `{PROVIDER_KEY_NAME[spec_['provider']]}` to the app secrets to use this engine.")

    with st.expander("❓ Help"):
        for q, a in FAQ.items():
            st.markdown(f"**{q}**  \n{a}")

    with st.popover("🧹 Start fresh", width="stretch"):
        st.caption("Deletes every ad in this studio. Your profile and settings are kept.")
        st.button("Yes, delete all ads", type="primary", on_click=reset_studio)

image_model = ss.image_model
video_engine = ss.video_engine
spec = VIDEO_ENGINES[video_engine]
img_spec = IMAGE_MODELS[image_model]


def _keep_nav() -> None:
    if not ss.nav:  # clicking the active step again would otherwise deselect it
        ss.nav = ss.get("last_step", NAV[0])


first_name = (PROFILE.get("name") or HANDLE).split()[0]
st.title(f"Hi {first_name}! 👋")
step = st.segmented_control("Step", NAV, key="nav", label_visibility="collapsed", on_change=_keep_nav)
step = step or ss.get("last_step", NAV[0])
ss.last_step = step


# ===========================================================================
# ① CREATE
# ===========================================================================
def use_sample() -> None:
    ss.sample_on = True
    ss.p_name, ss.p_offer = SAMPLE["name"], SAMPLE["offer"]
    ss.ideas = None


def use_own_photo() -> None:
    ss.sample_on = False
    ss.p_name = ss.p_offer = ""
    ss.ideas = None


def pick_style(key: str) -> None:
    ss.style_pick = key


def use_idea(idea: dict) -> None:
    ss.style_pick = idea["style"]
    ss.p_direction = idea["direction"]
    ss.hook_hint = idea["hook"]


def page_create() -> None:
    ss.setdefault("sample_on", False)
    ss.setdefault("p_formats", list(PLATFORMS[PROFILE.get("platform", "Instagram")]))
    ss.setdefault("style_pick", NICHE["style"])

    if ss.campaigns:
        # Results first: what you just made is what you want to see.
        camp = pick_campaign("Your ad sets")
        gallery(camp)
        st.divider()
        with st.expander("➕ Make more ads", expanded=False):
            create_form()
    else:
        if not ss.sample_on:
            with st.container(border=True):
                w1, w2 = st.columns([3, 1], vertical_alignment="center")
                w1.markdown("**Let's make your first ad.** Add a product, pick a look and post it. It takes about a "
                            "minute, and you don't need a camera.")
                w2.button("▶ Try a sample product", type="primary", on_click=use_sample, width="stretch")
        create_form()


def create_form() -> None:
    section("1", "Your product")
    left, right = st.columns([3, 1], gap="large")
    with left:
        product_jpeg = None
        if ss.sample_on:
            product_jpeg = studio.prepare_product((ASSETS / "sample_product.jpg").read_bytes())
            st.info("Using a sample product so you can see how it works.", icon="🧪")
            st.button("Use my own photo instead", on_click=use_own_photo)
        else:
            upload = st.file_uploader("Product photo", type=["jpg", "jpeg", "png", "webp"],
                                      help="A plain phone photo is fine. The AI keeps your real product and builds "
                                           "the scene around it.")
            if upload:
                try:
                    product_jpeg = studio.prepare_product(upload.getvalue())
                except studio.StudioError as err:
                    st.error(str(err))
        a, b = st.columns(2)
        product_name = a.text_input("Product name", key="p_name", placeholder="Glow Serum 30ml")
        offer = b.text_input("Offer or best feature (optional)", key="p_offer", placeholder="20% off this week")
    with right:
        if product_jpeg:
            st.image(product_jpeg, caption="Your product", width="stretch")

    section("2", "Pick a look")
    i1, i2 = st.columns([3, 1], vertical_alignment="center")
    i1.caption("Not sure? Let the AI suggest three ideas for your product.")
    if i2.button("💡 Give me ideas", width="stretch", disabled=not product_jpeg):
        with st.spinner("Thinking up ideas…"):
            try:
                ss.ideas = studio.suggest_ideas(client, product_jpeg, product_name, PROFILE.get("niche", ""))
            except studio.StudioError as err:
                st.error(str(err))
    if ss.get("ideas"):
        icols = st.columns(len(ss.ideas))
        for col, idea in zip(icols, ss.ideas):
            with col.container(border=True):
                st.markdown(f"**{idea['title']}**")
                st.caption(f"{AD_STYLES[idea['style']]['name']} · {idea['direction']}")
                st.markdown(f"*“{idea['hook']}”*")
                st.button("Use this idea", key=f"idea-{idea['title']}", on_click=use_idea, args=(idea,),
                          width="stretch")

    scols = st.container(key="styles").columns(len(AD_STYLES))
    for col, (key, sty) in zip(scols, AD_STYLES.items()):
        with col:
            st.image(str(ASSETS / "styles" / f"{key}.jpg"), width="stretch")
            chosen = ss.style_pick == key
            st.button(("✓ " if chosen else "") + sty["name"], key=f"sty-{key}", on_click=pick_style, args=(key,),
                      type="primary" if chosen else "secondary", width="stretch", help=sty["blurb"])
    style = ss.style_pick
    direction = st.text_input("Describe the scene (optional)", key="p_direction",
                              placeholder="e.g. pastel colours, beach at golden hour")

    section("3", "Where will you post?")
    aspects = st.pills("Formats", list(FORMATS), selection_mode="multi", key="p_formats",
                       format_func=lambda k: f"{FORMATS[k]} · {k}", label_visibility="collapsed") or []
    with st.expander("More options"):
        o1, o2, o3 = st.columns(3)
        want_video = o1.toggle("Make videos", value=True, help="Animate the vertical and landscape images into videos")
        want_copy = o2.toggle("Write my caption", value=True)
        want_overlay = o3.toggle("Headline & button on images", value=True, disabled=not want_copy)
        audience = st.text_input("Who is it for? (optional)", value=NICHE["audience"])

    video_ready = KEYS.has(spec["provider"])
    video_aspects = [x for x in aspects if x in spec["aspects"]] if want_video and video_ready else []

    st.divider()
    g1, g2 = st.columns([3, 1], vertical_alignment="center")
    parts = [f"{len(aspects)} image ad{'s' * (len(aspects) != 1)}"]
    if video_aspects:
        parts.append(f"{len(video_aspects)} video{'s' * (len(video_aspects) != 1)}")
    if want_copy:
        parts.append("caption")
    g1.markdown(f"You'll get **{' + '.join(parts)}** · {'about 1 minute' if video_aspects else 'about 15 seconds'}")
    if want_video and not video_ready:
        g1.caption(f"Videos skipped: {spec['label']} isn't connected. Pick a Veo engine in ⚙️ Settings.")
    hint = None
    if not product_jpeg:
        hint = "Add a product photo (or try the sample) to start."
    elif not aspects:
        hint = "Pick at least one format."
    go = g2.button("✨ Make my ads", type="primary", width="stretch", disabled=hint is not None)
    if hint:
        g2.caption(hint)

    if go and product_jpeg:
        generate_campaign(product_jpeg, product_name, offer, audience, style, direction, aspects, video_aspects,
                          want_copy, want_overlay)
        st.rerun()


def generate_campaign(product_jpeg, product_name, offer, audience, style, direction, aspects, video_aspects,
                      want_copy, want_overlay) -> None:
    camp = {
        "id": new_id(), "product": product_name or "My product", "offer": offer, "style": style,
        "direction": direction, "copy": None, "overlay": want_overlay and want_copy, "items": [], "errors": [],
        "product_jpeg": product_jpeg, "finals": {}, "created": datetime.now().strftime("%b %d, %H:%M"),
    }
    with st.status("Making your ads…", expanded=True) as status:
        tip = st.empty()
        jobs = {f"img:{a}": (lambda a=a: studio.generate_image(client, image_model, product_jpeg, style, a, direction))
                for a in aspects}
        if want_copy:
            jobs["copy"] = lambda: studio.write_ad_copy(client, product_jpeg, product_name, offer, audience, style,
                                                        ss.get("hook_hint", ""))
        st.write(f"🎨 Setting up the scene for {len(aspects)} image ad(s)"
                 + (" and writing your caption" if want_copy else ""))
        res = run_parallel(jobs, status, "Designing your images", tip)

        copy = res.get("copy")
        if isinstance(copy, studio.StudioError):
            camp["errors"].append(f"Caption: {copy}")
            copy = None
        camp["copy"] = copy
        camp["overlay"] = camp["overlay"] and bool(copy)

        stills = {}
        for a in aspects:
            r = res[f"img:{a}"]
            if isinstance(r, studio.StudioError):
                camp["errors"].append(f"{FORMATS[a]} image: {r}")
                continue
            it = {"id": new_id(), "kind": "image", "aspect": a, "raw": r.data, "mime": r.mime, "seconds": r.seconds,
                  "model": img_spec["label"], "versions": [{"raw": r.data, "mime": r.mime, "note": "Original"}],
                  "v_idx": 0}
            compose(camp, it)
            camp["items"].append(it)
            stills[a] = it

        vids = [a for a in video_aspects if a in stills]
        if vids:
            st.write(f"🎬 Filming {len(vids)} video(s). This usually takes under a minute.")
            prompt = studio.video_prompt(style, direction)
            vres = run_parallel(
                {a: (lambda a=a: studio.generate_video(video_engine, KEYS, stills[a]["raw"], stills[a]["mime"],
                                                       prompt, a)) for a in vids},
                status, "Filming your videos", tip,
            )
            for a, r in vres.items():
                if isinstance(r, studio.StudioError):
                    camp["errors"].append(f"{FORMATS[a]} video: {r}")
                    continue
                camp["items"].append(video_item(r, a, spec, video_engine, stills[a]["raw"], stills[a]["mime"],
                                                prompt, "Take 1"))
        tip.empty()
        ok = len(camp["items"])
        status.update(label=f"🎉 {ok} ad{'s' * (ok != 1)} ready!"
                      + (f" · {len(camp['errors'])} didn't work" if camp["errors"] else ""),
                      state="complete" if ok else "error", expanded=False)
    ss.pop("hook_hint", None)
    if camp["items"] or camp["errors"]:
        ss.campaigns.insert(0, camp)
        ss.active = camp["id"]
        if ok:
            st.balloons()


ASPECT_ORDER = {"9:16": 0, "1:1": 1, "16:9": 2}
COL_WEIGHT = {"9:16": 1, "1:1": 1.2, "16:9": 1.9}


def gallery(camp: dict) -> None:
    head, kit, rm = st.columns([4, 1.3, 1], vertical_alignment="center")
    head.subheader(f"{camp['product']} · {AD_STYLES[camp['style']]['name']}")
    kit_key = f"kit-{camp['id']}-{len(camp['items'])}-{len(camp['finals'])}"
    if ss.get("kit_key") == kit_key:
        kit.download_button("📦 Download all", ss.kit_data, file_name=f"risus-{slug(camp['product'])}-share-kit.zip",
                            mime="application/zip", width="stretch", type="primary")
    elif kit.button("📦 Prepare share kit", width="stretch", help="All images, videos and your caption in one zip"):
        ss.kit_key, ss.kit_data = kit_key, share_kit(camp)
        st.rerun()
    with rm.popover("🗑️ Delete", width="stretch"):
        st.caption("Delete this ad set and everything in it?")
        if st.button("Yes, delete", key=f"rm-{camp['id']}", type="primary"):
            ss.campaigns.remove(camp)
            st.rerun()
    for err in camp["errors"]:
        st.warning(err)

    if camp["copy"]:
        st.markdown(f"<div class='meta'>YOUR HOOK (say or show it in the first 2 seconds)</div>"
                    f"<div class='hook'>“{camp['copy']['hook']}”</div>", unsafe_allow_html=True)
    st.caption("Your caption, with your tracked link. Use the copy icon in the corner.")
    st.code(caption_for(camp), language=None, wrap_lines=True)
    with st.expander("✅ Before you post"):
        for i, c in enumerate(CHECKLIST):
            st.checkbox(c, key=f"chk-{camp['id']}-{i}")

    items = sorted(camp["items"], key=lambda i: (ASPECT_ORDER[i["aspect"]], i["kind"] != "image"))
    if not items:
        return
    cols = st.columns([COL_WEIGHT[i["aspect"]] for i in items], gap="medium")
    for col, it in zip(cols, items):
        with col:
            if it["kind"] == "image":
                st.image(it["data"], width="stretch")
            else:
                st.video(it["data"], format="video/mp4", loop=True, autoplay=True, muted=True)
            st.markdown(f"<span class='ai-pill'>AI-generated</span><b>{item_title(it)}</b><br>"
                        f"<span class='meta'>Made in {it['seconds']:.0f}s</span>", unsafe_allow_html=True)
            name = f"risus-{slug(camp['product'])}-{camp['style']}-{it['aspect'].replace(':', 'x')}-{it['id']}"
            st.download_button("⬇ Download", it["data"], file_name=f"{name}.{'jpg' if it['kind'] == 'image' else 'mp4'}",
                               mime="image/jpeg" if it["kind"] == "image" else "video/mp4",
                               key=f"dl-{it['id']}", width="stretch")
            if it["kind"] == "image":
                st.button("✏️ Edit this image", key=f"ed-{it['id']}", width="stretch",
                          on_click=goto, args=(NAV[1],), kwargs={"edit_image": it["id"], "active": camp["id"]})
                usable = usable_engines(it["aspect"])
                if usable:
                    eng = video_engine if video_engine in usable else usable[0]
                    label = "🎬 Make another video" if videos_of(camp, it["aspect"]) else "🎬 Turn into video"
                    if st.button(label, key=f"anim-{it['id']}", width="stretch"):
                        n = len(videos_of(camp, it["aspect"])) + 1
                        if make_video(camp, it["raw"], it["mime"], it["aspect"], eng,
                                      studio.video_prompt(camp["style"], camp["direction"]), f"Take {n}"):
                            st.rerun()
            else:
                st.button("🎚️ Add sound & finish", key=f"fc-{it['id']}", width="stretch", type="primary",
                          on_click=goto, args=(NAV[2],), kwargs={"active": camp["id"], "cut_aspect": it["aspect"]})


# ===========================================================================
# ② EDIT
# ===========================================================================
def page_edit() -> None:
    camp = pick_campaign()
    if not camp:
        st.info("Make your first ads in ① Create. They'll appear here for editing.")
        st.button("Go to ① Create", on_click=goto, args=(NAV[0],), type="primary")
        return

    tab_img, tab_vid, tab_copy = st.tabs(["🖼️ Edit images", "🎬 Remix videos", "✍️ Edit caption"])

    with tab_img:
        imgs = images_of(camp)
        if not imgs:
            st.info("This ad set has no images.")
        else:
            ids = [i["id"] for i in imgs]
            if ss.get("edit_image") not in ids:
                ss.edit_image = ids[0]
            if len(ids) > 1:
                ss.edit_image = st.radio("Which image?", ids, index=ids.index(ss.edit_image), horizontal=True,
                                         format_func=lambda i: item_title(next(x for x in imgs if x["id"] == i)))
            it = next(x for x in imgs if x["id"] == ss.edit_image)

            left, right = st.columns([2, 3], gap="large")
            with left:
                st.image(it["data"], width="stretch")
                if len(it["versions"]) > 1:
                    st.caption("Earlier versions. Tap one to go back to it.")
                    shown = it["versions"][-5:]
                    offset = len(it["versions"]) - len(shown)
                    vcols = st.columns(5)
                    for vi, v in enumerate(shown):
                        real = offset + vi
                        with vcols[vi]:
                            st.image(v["raw"], width="stretch")
                            if real == it["v_idx"]:
                                st.caption(f"**v{real + 1}** · now")
                            elif st.button(f"v{real + 1}", key=f"use-{it['id']}-{real}", width="stretch",
                                           help=v["note"]):
                                it["v_idx"], it["raw"], it["mime"] = real, v["raw"], v["mime"]
                                compose(camp, it)
                                st.rerun()

            with right:
                st.markdown("##### What would you like to change?")
                idea = st.pills("Ideas", NICHE["ideas"], key=f"idea-{it['id']}", label_visibility="collapsed")
                instruction = st.text_input("Change", value=idea or "", key=f"instr-{it['id']}-{idea}",
                                            placeholder="e.g. make the background pastel pink",
                                            label_visibility="collapsed")
                st.caption("Describe it like you'd tell a photographer. Your product stays the same.")
                if st.button("✨ Make this change", type="primary", disabled=not instruction.strip(),
                             key=f"apply-{it['id']}"):
                    with st.spinner("Re-shooting your image…"):
                        try:
                            r = studio.edit_image(client, image_model, it["raw"], it["mime"], camp["product_jpeg"],
                                                  instruction.strip(), it["aspect"])
                        except studio.StudioError as err:
                            st.error(str(err))
                            r = None
                    if r:
                        it["versions"].append({"raw": r.data, "mime": r.mime, "note": instruction.strip()})
                        it["v_idx"], it["raw"], it["mime"] = len(it["versions"]) - 1, r.data, r.mime
                        compose(camp, it)
                        st.rerun()

                st.markdown("##### Text on the image")
                copy = camp["copy"] or {"hook": "", "headline": "", "caption": "", "cta": "", "hashtags": []}
                on = st.toggle("Show headline and button", value=camp["overlay"], key=f"ov-{camp['id']}")
                h1, h2 = st.columns([3, 2])
                headline = h1.text_input("Headline", value=copy.get("headline", ""), key=f"hl-{camp['id']}",
                                         disabled=not on)
                cta = h2.text_input("Button text", value=copy.get("cta", ""), key=f"cta-{camp['id']}", disabled=not on)
                if (on, headline, cta) != (camp["overlay"], copy.get("headline", ""), copy.get("cta", "")):
                    copy.update(headline=headline, cta=cta)
                    camp["copy"], camp["overlay"] = copy, on
                    for x in images_of(camp):
                        compose(camp, x)
                    st.rerun()
                st.caption("Changes instantly on every image in this ad set. Change the button colour in ⚙️ Settings.")

                usable = usable_engines(it["aspect"])
                if usable:
                    st.markdown("##### Turn this version into a video")
                    eng = video_engine if video_engine in usable else usable[0]
                    if st.button(f"🎬 Make a video from v{it['v_idx'] + 1}", key=f"anim-edit-{it['id']}"):
                        n = len(videos_of(camp, it["aspect"])) + 1
                        if make_video(camp, it["raw"], it["mime"], it["aspect"], eng,
                                      studio.video_prompt(camp["style"], camp["direction"]), f"Take {n}"):
                            st.toast("New video added! Find it in Remix videos or ③ Add sound & finish.", icon="🎬")
                            st.rerun()

    with tab_vid:
        vids = videos_of(camp)
        if not vids:
            st.info("No videos yet. Turn an image into a video first.")
        else:
            ids = [v["id"] for v in vids]
            sel = ids[0]
            if len(ids) > 1:
                sel = st.radio("Which video?", ids, horizontal=True, key=f"vid-pick-{camp['id']}",
                               format_func=lambda i: item_title(next(x for x in vids if x["id"] == i)))
            v = next(x for x in vids if x["id"] == sel)
            left, right = st.columns([2, 3], gap="large")
            with left:
                st.video(v["data"], format="video/mp4", loop=True, autoplay=True, muted=True)
                st.caption(f"{item_title(v)} · {v['duration']:.1f}s")
            with right:
                st.markdown("##### How should it move instead?")
                motion = st.pills("Motion ideas", MOTION_IDEAS, key=f"motion-{v['id']}", label_visibility="collapsed")
                change = st.text_input("What should change?", value=motion or "", key=f"remix-{v['id']}-{motion}",
                                       placeholder="e.g. slower camera, more sparkle, end close on the label",
                                       label_visibility="collapsed")
                usable = usable_engines(v["aspect"])
                eng = video_engine if video_engine in usable else (usable[0] if usable else None)
                img_match = next((x for x in images_of(camp) if x["aspect"] == v["aspect"]), None)
                use_latest = False
                if img_match and img_match["v_idx"] > 0:
                    use_latest = st.toggle(f"Start from my edited image (v{img_match['v_idx'] + 1})", value=True,
                                           key=f"latest-{v['id']}")
                st.caption("You keep this take too, so you can compare them in ③.")
                if eng and st.button("🔁 Make a new take", type="primary", disabled=not change.strip(),
                                     key=f"remix-go-{v['id']}"):
                    src_raw, src_mime = ((img_match["raw"], img_match["mime"]) if use_latest
                                         else (v["src_raw"], v["src_mime"]))
                    prompt = f"{studio.video_prompt(camp['style'], camp['direction'])} Change: {change.strip()}."
                    n = len(videos_of(camp, v["aspect"])) + 1
                    if make_video(camp, src_raw, src_mime, v["aspect"], eng, prompt, f"Take {n}"):
                        st.rerun()
                with st.popover("🗑️ Delete this take"):
                    if st.button("Yes, delete it", key=f"del-{v['id']}", type="primary"):
                        camp["items"].remove(v)
                        st.rerun()

    with tab_copy:
        copy = camp["copy"] or {"hook": "", "headline": "", "caption": "", "cta": "", "hashtags": []}
        hook = st.text_input("Hook (first 2 seconds)", value=copy.get("hook", ""), key=f"hook-{camp['id']}")
        caption = st.text_area("Caption", value=copy.get("caption", ""), key=f"cap-{camp['id']}")
        tags = st.text_input("Hashtags", value=" ".join(copy.get("hashtags", [])), key=f"tags-{camp['id']}")
        c1, c2 = st.columns(2)
        if c1.button("💾 Save", type="primary", width="stretch"):
            copy.update(hook=hook, caption=caption, hashtags=[t.lstrip("#") for t in tags.split() if t.strip("#")])
            camp["copy"] = copy
            st.toast("Saved")
        if c2.button("✍️ Write a new version", width="stretch"):
            with st.spinner("Writing…"):
                try:
                    camp["copy"] = studio.write_ad_copy(client, camp["product_jpeg"], camp["product"], camp["offer"],
                                                        NICHE["audience"], camp["style"])
                except studio.StudioError as err:
                    st.error(str(err))
                else:
                    for k in (f"hook-{camp['id']}", f"cap-{camp['id']}", f"tags-{camp['id']}",
                              f"hl-{camp['id']}", f"cta-{camp['id']}"):
                        ss.pop(k, None)
                    for x in images_of(camp):
                        compose(camp, x)
                    st.rerun()
        st.markdown("**Preview**")
        st.code(caption_for(camp), language=None, wrap_lines=True)


# ===========================================================================
# ③ ADD SOUND & FINISH
# ===========================================================================
def page_cut() -> None:
    camp = pick_campaign()
    if not camp or not videos_of(camp):
        st.info("You need at least one video. Make one in ① Create, or turn an image into a video in ② Edit.")
        st.button("Go to ① Create", on_click=goto, args=(NAV[0],), type="primary")
        return
    cid = camp["id"]
    copy = camp["copy"] or {}

    aspects = [a for a in FORMATS if videos_of(camp, a)]
    if ss.get("cut_aspect") not in aspects:
        ss.cut_aspect = aspects[0]
    aspect = aspects[0]
    if len(aspects) > 1:
        aspect = st.segmented_control("Format", aspects, key="cut_aspect",
                                      format_func=lambda a: f"{FORMATS[a]} · {a}") or aspects[0]

    edit_col, out_col = st.columns([3, 2], gap="large")
    with edit_col:
        section("1", "Clips")
        vids = videos_of(camp, aspect)
        chosen = [vids[0]["id"]]
        if len(vids) > 1:
            chosen = st.multiselect("Which takes, in order?", [v["id"] for v in vids], default=[vids[0]["id"]],
                                    format_func=lambda i: next(x for x in vids if x["id"] == i)["label"],
                                    key=f"clips-{cid}-{aspect}")
        else:
            st.caption("Make more takes in ② Edit → Remix videos to combine them here.")
        clips, total = [], 0.0
        for vid_id in chosen:
            v = next(x for x in vids if x["id"] == vid_id)
            s, e = st.slider(f"Trim {v['label']}", 0.0, float(v["duration"]), (0.0, float(v["duration"])),
                             step=0.5, key=f"trim-{v['id']}", format="%.1fs")
            if e - s >= 0.5:
                clips.append(editor.Clip(v["data"], s, e))
                total += e - s

        section("2", "Text")
        t1, t2 = st.columns(2)
        show_hook = t1.toggle("Hook at the start", value=True, key=f"hk-on-{cid}",
                              help=f"Shown for the first {editor.HOOK_SECONDS:.1f} seconds")
        show_card = t2.toggle("End card with your link", value=True, key=f"ec-on-{cid}",
                              help=f"{editor.END_CARD_SECONDS:.1f}s packshot with headline, button and your link")
        hook = card_head = card_cta = ""
        if show_hook:
            hook = st.text_input("Hook text", value=copy.get("hook", ""), key=f"hk-{cid}")
        if show_card:
            e1, e2 = st.columns([3, 2])
            card_head = e1.text_input("End card headline", value=copy.get("headline", camp["product"]),
                                      key=f"ech-{cid}")
            card_cta = e2.text_input("End card button", value=copy.get("cta", "Shop now"), key=f"ecc-{cid}")
            total += editor.END_CARD_SECONDS

        section("3", "Your voice")
        vmode = st.segmented_control("Voice", ["None", "🎙️ Record my voice", "🤖 AI voice", "⬆️ Upload"],
                                     default="🎙️ Record my voice", key=f"vm-{cid}",
                                     label_visibility="collapsed") or "None"
        voice = None
        script = ""
        if vmode in ("🎙️ Record my voice", "🤖 AI voice"):
            default_script = f"{copy.get('hook', camp['product'])} Link below!".strip()
            script = st.text_area("Script", value=default_script, key=f"script-{cid}", height=80,
                                  help="Edit it to sound like you.")
            est = len(script.split()) / 2.6
            st.caption(f"About {est:.0f}s to say · your ad is {total:.1f}s"
                       + (" · ⚠️ a bit long: shorten it or add a take" if est > total else " · 👍 fits"))
        if vmode == "🎙️ Record my voice":
            rec = st.audio_input("Press the mic and read the script", key=f"rec-{cid}")
            voice = rec.getvalue() if rec else None
        elif vmode == "🤖 AI voice":
            a1, a2 = st.columns([2, 1], vertical_alignment="bottom")
            vname = a1.selectbox("Voice", list(studio.VOICES), index=list(studio.VOICES).index(ss.voice_pref),
                                 format_func=lambda k: f"{k} · {studio.VOICES[k]}", key=f"vn-{cid}")
            if a2.button("Create voice", key=f"tts-{cid}", width="stretch"):
                with st.spinner("Recording the voiceover…"):
                    try:
                        data, mime = studio.synthesize_voice(client, script, vname)
                        camp["ai_voice"] = {"data": data, "mime": mime, "voice": vname}
                    except studio.StudioError as err:
                        st.error(str(err))
            if camp.get("ai_voice"):
                st.audio(camp["ai_voice"]["data"], format=camp["ai_voice"]["mime"])
                voice = camp["ai_voice"]["data"]
        elif vmode == "⬆️ Upload":
            up = st.file_uploader("Voiceover file", type=["wav", "mp3", "m4a", "ogg", "webm"], key=f"vup-{cid}")
            voice = up.getvalue() if up else None

        section("4", "Music")
        mmode = st.segmented_control("Music", ["None", "🎵 Make music for me", "⬆️ My own track"], default="None",
                                     key=f"mm-{cid}", label_visibility="collapsed") or "None"
        music = None
        if mmode == "🎵 Make music for me":
            m1, m2 = st.columns([2, 1], vertical_alignment="bottom")
            mood = m1.text_input("Mood", value=studio.MUSIC_MOODS[camp["style"]], key=f"mood-{cid}")
            if m2.button("Create music", key=f"lyria-{cid}", width="stretch"):
                with st.spinner("Composing your track (about 20 seconds)…"):
                    try:
                        data, mime = studio.generate_music(client, mood)
                        camp["ai_music"] = {"data": data, "mime": mime}
                    except studio.StudioError as err:
                        st.error(str(err))
            if camp.get("ai_music"):
                st.audio(camp["ai_music"]["data"], format=camp["ai_music"]["mime"])
                music = camp["ai_music"]["data"]
            st.caption("Original, royalty-free music made by Google Lyria. Safe to use in ads.")
        elif mmode == "⬆️ My own track":
            up = st.file_uploader("Music file", type=["mp3", "wav", "m4a", "ogg"], key=f"mup-{cid}")
            music = up.getvalue() if up else None
            st.caption("Only upload music you have the rights to use in ads.")

        section("5", "Volume")
        silent = st.toggle("No sound: I'll add a trending sound in TikTok or Instagram", key=f"silent-{cid}")
        vol_orig, vol_voice, vol_music = 1.0, 1.0, 0.4
        if not silent:
            sliders = st.columns(1 + bool(voice) + bool(music))
            vol_orig = sliders[0].slider("Video's own sound", 0, 150, 30 if (voice or music) else 100, 5,
                                         key=f"vo-{cid}-{bool(voice or music)}", format="%d%%") / 100
            i = 1
            if voice:
                vol_voice = sliders[i].slider("Voice", 0, 150, 100, 5, key=f"vv-{cid}", format="%d%%") / 100
                i += 1
            if music:
                vol_music = sliders[i].slider("Music", 0, 150, 40 if voice else 70, 5, key=f"vm2-{cid}-{bool(voice)}",
                                              format="%d%%") / 100

    with out_col:
        with st.container(border=True):
            st.markdown("##### Your finished ad")
            st.metric("Length", f"{total:.1f}s")
            if st.button("🎬 Make my final ad", type="primary", width="stretch", disabled=not clips):
                still = next((x for x in images_of(camp) if x["aspect"] == aspect), None)
                card_still = still["raw"] if still else next(x for x in vids if x["id"] == chosen[0])["src_raw"]
                with st.spinner("Putting it all together…"):
                    try:
                        camp["finals"][aspect] = editor.render_final(
                            clips, aspect, hook=hook,
                            end_card={"still": card_still, "headline": card_head, "cta": card_cta,
                                      "link": f"risus.app/r/{HANDLE}", "accent": ss.accent} if show_card else None,
                            voice=voice, music=music,
                            vol_original=vol_orig, vol_voice=vol_voice, vol_music=vol_music, silent=silent,
                        )
                        st.toast("Your ad is ready!", icon="🎉")
                    except editor.EditorError as err:
                        st.error(str(err))
            final = camp["finals"].get(aspect)
            if final:
                st.video(final, format="video/mp4")
                st.download_button("⬇ Download final ad", final, type="primary", width="stretch",
                                   file_name=f"risus-{slug(camp['product'])}-final-{aspect.replace(':', 'x')}.mp4",
                                   mime="video/mp4")
                st.markdown("<span class='ai-pill'>AI-generated</span><span class='meta'>The label is already in "
                            "the video. Also switch on the platform's AI toggle when you post.</span>",
                            unsafe_allow_html=True)
                st.caption("Caption to post with it")
                st.code(caption_for(camp, aspect), language=None, wrap_lines=True)
                with st.expander("✅ Before you post"):
                    for c in CHECKLIST:
                        st.markdown(f"- {c}")
            else:
                st.caption("Choose your text, voice and music, then press **Make my final ad**.")


# ===========================================================================
# 📈 MY RESULTS (sample data)
# ===========================================================================
SAMPLE_CREATORS = ["@lena.glow", "@dami.daily", "@sofia.finds", "@kai.unboxes", "@noor.home"]


def sample_rows() -> pd.DataFrame:
    rows = []
    for camp in ss.campaigns:
        for it in camp["items"]:
            label = f"{it['aspect']} image v{it['v_idx'] + 1}" if it["kind"] == "image" else f"{it['aspect']} {it['label']}"
            rows.append((f"{camp['product']} · {label}", it["kind"], it["id"]))
        for aspect in camp["finals"]:
            rows.append((f"{camp['product']} · {aspect} final ad", "final ad", f"{camp['id']}-{aspect}"))
    if not rows:  # before anything is made
        rows = [(f"Sample ad {i + 1}", k, f"s{i}") for i, k in enumerate(["video", "image", "video", "image", "video"])]
    data = []
    for name, kind, rid in rows:
        rnd = random.Random(rid)
        views = rnd.randint(2500, 12000) if kind == "image" else rnd.randint(9000, 42000)
        clicks = int(views * rnd.uniform(0.012, 0.018 if kind == "image" else 0.03))
        data.append({"Ad": name, "Type": kind, "Views": views, "Link clicks": clicks,
                     "New users": int(clicks * rnd.uniform(0.08, 0.18))})
    return pd.DataFrame(data)


def page_results() -> None:
    st.info("**Sample data for the demo.** In the pilot these numbers come from each ad's tracked Risus link.",
            icon="📊")
    df = sample_rows()
    users = int(df["New users"].sum())

    k = st.columns(4)
    k[0].metric("Ads", len(df))
    k[1].metric("Views", f"{int(df['Views'].sum()):,}")
    k[2].metric("Link clicks", f"{int(df['Link clicks'].sum()):,}")
    k[3].metric("People who joined Risus", f"{users:,}")

    rnd = random.Random(users)
    weights = [rnd.uniform(0.4, 1.0) * (1 + i / 10) for i in range(14)]
    days = [date.today() - timedelta(days=13 - i) for i in range(14)]
    daily = pd.DataFrame({"Day": days, "New users": [round(users * w / sum(weights)) for w in weights]})

    c1, c2 = st.columns(2, gap="large")
    with c1:
        st.markdown("**People who joined through your links · last 14 days**")
        line = alt.Chart(daily).mark_line(color=ss.accent, strokeWidth=2,
                                          point=alt.OverlayMarkDef(size=60, color=ss.accent)) \
            .encode(x=alt.X("Day:T", title=None, axis=alt.Axis(format="%b %d", grid=False)),
                    y=alt.Y("New users:Q", title=None, axis=alt.Axis(gridOpacity=0.3)),
                    tooltip=[alt.Tooltip("Day:T", format="%a %b %d"), "New users:Q"]).properties(height=280)
        st.altair_chart(line, width="stretch")
    with c2:
        st.markdown("**Your best ads**")
        top = df.sort_values("New users", ascending=False).head(8)
        bars = alt.Chart(top).mark_bar(color=ss.accent, cornerRadiusEnd=4, size=18) \
            .encode(x=alt.X("New users:Q", title=None, axis=alt.Axis(gridOpacity=0.3)),
                    y=alt.Y("Ad:N", sort="-x", title=None, axis=alt.Axis(labelLimit=260)),
                    tooltip=["Ad", "Type", "Views", "Link clicks", "New users"]).properties(height=280)
        st.altair_chart(bars, width="stretch")

    st.markdown("**Creator leaderboard**")
    board = [{"Creator": f"@{HANDLE} (you)", "Ads": len(df), "New users": users}]
    for name in SAMPLE_CREATORS:
        r = random.Random(name)
        board.append({"Creator": name, "Ads": r.randint(3, 24), "New users": r.randint(15, 180)})
    st.dataframe(pd.DataFrame(board).sort_values("New users", ascending=False), hide_index=True, width="stretch")

    with st.expander("Every ad"):
        st.dataframe(df, hide_index=True, width="stretch")


# ---------------------------------------------------------------------------
PAGES = {NAV[0]: page_create, NAV[1]: page_edit, NAV[2]: page_cut, NAV[3]: page_results}
PAGES[step]()
persist()
