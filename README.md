# Risus Studio: product ad generator

A Streamlit demo of the Risus Studio idea from the R&D doc. It turns one product photo into finished, labelled social ads, and it's built so a creator can make their first ad in about a minute without reading instructions.

**Getting started:** the app opens straight into the studio. A welcome screen explains the three steps, and **▶ Try it with a sample product** shows the whole flow without uploading anything.

| Step | What the creator does |
|---|---|
| **① Create** | Add a product photo and name. Pick a look from **visual style cards**, or tap **💡 Give me ideas** to get three AI-suggested concepts for the product. Choose where to post. You get image ads, videos, a hook and a caption with the tracked link. Then use **📦 Prepare share kit** to download everything as one zip, and tick through the **✅ Before you post** checklist. |
| **② Edit** | Edit an image by describing the change; idea chips are tailored to the creator's niche. Version history lets you go back. Change the headline and button text for free. Make new video takes. Rewrite the caption. |
| **③ Add sound & finish** | Pick and trim takes, then add a hook and an end card with the creator's link. Add a voice: record your own (the default), use an AI voice, or upload one. Add music: AI-made with Lyria, or your own track. Set the volume, or export silent and add a trending sound in the app. |
| **🎬 Cinematic ad** | Movie-style commercials (15 or 30s). Add the product photo, pick a feel (Dark & epic, Luxury & elegant, Bold & energetic, Warm & emotional), then describe the idea or paste a full script. The AI writes an **editable storyboard** (each scene has a shot, camera move with sound, and a voiceover line). **🎬 Shoot my film** then films every scene in parallel on Veo 3.1 and records each voiceover line in a cinematic narrator voice. It composes a score with Lyria and edits it all together: lines land on their scenes, plus 2.39:1 cinema bars and a film-title end frame. **🔁 Reshoot** any single scene, or re-cut the sound for free. A 15s film takes about 1–2 min; 30s takes 2–4 min. |
| **📈 My results** | Sample dashboard: views, clicks, people who joined Risus, the best ads and a creator leaderboard. |

**Sidebar:**
- Profile card with the creator's Risus link.
- **⚙️ Settings**: name, handle, niche, main platform, brand colour, default AI voice, and the AI models under "Advanced".
- **❓ Help** (FAQ) and **🧹 Start fresh**, which clears all ads before a demo.

The creator's niche and main platform set smart defaults: which look comes first, which formats are selected, and which edit ideas appear. Ads and the profile are saved to `data/`, so they survive page reloads. There's no login for now.

## Engines and costs

| Job | Engine | Secret | Approx. AI cost |
|---|---|---|---|
| Ad copy | Gemini Flash | `GEMINI_API_KEY` | < $0.01 |
| Image ads and edits | Nano Banana 2 / Pro | `GEMINI_API_KEY` | $0.07 / $0.14 |
| Video ads | Google Veo 3.1 Lite / Fast / premium | `GEMINI_API_KEY` | $0.40 / $0.80 / $3.20 |
| AI voiceover | Gemini 3.8 Flash TTS | `GEMINI_API_KEY` | < $0.01 |
| AI music | Google Lyria 3 (30s, royalty-free) | `GEMINI_API_KEY` | $0.04 |
| Final cut and film editing | ffmpeg (bundled via `imageio-ffmpeg`) | — | free |
| Cinematic ad (30s, Veo 3.1 Fast) | Storyboard + 6 scenes + voice + score | `GEMINI_API_KEY` | ~ $4–5 |
| Video ads (optional) | ByteDance Seedance 2.0 (via fal.ai) | `FAL_KEY` | ~ $1.10 |
| Video ads (optional) | Higgsfield (Kling 2.5 Turbo Pro) | `HIGGSFIELD_API_KEY` (`id:secret`) | ~ $0.55 |

The Gemini key alone runs everything. Seedance and Higgsfield show a 🔒 until you add their keys. Veo needs billing enabled on the Google AI Studio project.

## Run locally

```bash
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add your keys
streamlit run app.py
```

## Deploy to Streamlit Community Cloud (free)

1. Push this folder to a GitHub repo. `.streamlit/secrets.toml` is git-ignored, so your keys stay local.
2. At https://share.streamlit.io, click **Create app**, pick the repo, and set the main file to `app.py`.
3. Under **Advanced settings → Secrets**, paste:
   ```toml
   GEMINI_API_KEY = "..."
   APP_PASSWORD = "pick-a-password"
   # FAL_KEY = "..."
   # HIGGSFIELD_API_KEY = "id:secret"
   ```
4. Click **Deploy**.

**Set `APP_PASSWORD`.** Without it, anyone with the URL can spend your AI budget.

## 5-minute CEO demo

Before the meeting, click **🧹 Start fresh** in the sidebar, then set the creator's name and niche in ⚙️ Settings.

1. **Welcome screen:** show the three steps, then click **▶ Try it with a sample product**, or upload a real Risus product.
2. **① Create:** click **💡 Give me ideas** and **Use this idea**, or tap a style card. Click **✨ Make my ads**; it takes about a minute. Show the hook, the caption with the tracked link, **✅ Before you post** and **📦 Prepare share kit**.
3. **② Edit:** click **✏️ Edit this image** on the 9:16 ad, tap an idea chip such as "Pastel pink background", then **✨ Make this change**. Change the headline (free and instant). Under **🎬 Remix videos**, make a new take from the edited image.
4. **③ Add sound & finish:** select both takes and trim one. Record your voice (the default) or use **🤖 AI voice**. Add **🎵 Make music for me** and click **🎬 Make my final ad**. You get a 15-second ad with the hook, end card, link and AI label.
5. **📈 My results:** show the growth story: people who joined through the links, the best ads and the leaderboard.

Tip: make one ad set before the meeting so results are ready, then do one live edit and one final cut.

## Demo stand-ins (not production)

- There's no login: everyone using the app shares one studio. Data is saved in `data/`, which Streamlit Cloud wipes when the app restarts.
- The My results numbers are sample data, and `risus.app/r/...` is a placeholder tracked link.
- The visible AI label is burned into images and final cuts, and Veo adds Google's SynthID watermark. C2PA metadata isn't added yet.
- Not built yet: voice cloning, brand kits, brand approval, and direct posting to TikTok, Instagram and YouTube (next phase).
