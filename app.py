"""
Free One-Click Text-to-Video Tool
Gemini (script) + Pollinations (images) + edge-tts (voice) + MoviePy (video)

Setup: sirf `pip install streamlit` chahiye, baaki packages ye file khud install kar leti hai.
    set GEMINI_API_KEY=your_key      (Windows)
    export GEMINI_API_KEY=your_key   (Mac/Linux)
Run:
    streamlit run app.py
Note: FFmpeg should be installed (moviepy usually handles it via imageio-ffmpeg).
"""
import os, sys, json, asyncio, tempfile, textwrap, urllib.parse, subprocess, importlib

# ---- Auto-install: requirements.txt / packages.txt ki zaroorat nahi, sab is ek file se ho jayega ----
def _ensure(pip_name, module):
    try:
        importlib.import_module(module)
    except ImportError:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", pip_name])
        importlib.invalidate_caches()

for _pip, _mod in [("requests", "requests"), ("pillow", "PIL"), ("edge-tts", "edge_tts"),
                   ("google-genai", "google.genai"), ("imageio-ffmpeg", "imageio_ffmpeg"),
                   ("moviepy>=2.0", "moviepy")]:
    _ensure(_pip, _mod)

import requests, streamlit as st
import edge_tts
from google import genai
from PIL import Image, ImageDraw, ImageFont
from moviepy import ImageClip, AudioFileClip, CompositeAudioClip, concatenate_videoclips
from moviepy.audio.fx import AudioLoop, MultiplyVolume

# Hindi font khud download kar leta hai (cloud par font nahi hota)
FONT_PATH = os.path.join(tempfile.gettempdir(), "NotoSansDevanagari.ttf")
def _get_font():
    if not os.path.exists(FONT_PATH):
        for u in ["https://github.com/google/fonts/raw/main/ofl/notosansdevanagari/NotoSansDevanagari%5Bwdth%2Cwght%5D.ttf",
                  "https://github.com/notofonts/devanagari/raw/main/fonts/NotoSansDevanagari/hinted/ttf/NotoSansDevanagari-Regular.ttf"]:
            try:
                r = requests.get(u, timeout=30)
                if r.ok and len(r.content) > 50000:
                    open(FONT_PATH, "wb").write(r.content)
                    break
            except Exception:
                pass
    return FONT_PATH if os.path.exists(FONT_PATH) else None

# Cloud (Streamlit/HF) par key secrets se uthao, laptop par environment variable se
try:
    if "GEMINI_API_KEY" in st.secrets and "GEMINI_API_KEY" not in os.environ:
        os.environ["GEMINI_API_KEY"] = st.secrets["GEMINI_API_KEY"]
except Exception:
    pass

GEMINI_MODEL = "gemini-2.5-flash"   # agar model band ho jaye toh AI Studio se naya naam daalein
W, H = 720, 1280                    # vertical (Reels/Shorts). Landscape ke liye 1280, 720
VOICES = {"Hindi (Female)": "hi-IN-SwaraNeural", "Hindi (Male)": "hi-IN-MadhurNeural",
          "English (Female)": "en-US-JennyNeural", "English (Male)": "en-US-GuyNeural"}


def get_scenes(topic, n_scenes, language):
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = f"""Create a short video script about: "{topic}".
Return ONLY JSON: a list of {n_scenes} objects with keys
"narration" (1-2 short sentences in {language}) and
"image_prompt" (English, vivid visual description, no text in image)."""
    r = client.models.generate_content(
        model=GEMINI_MODEL, contents=prompt,
        config={"response_mime_type": "application/json"})
    return json.loads(r.text)


def scenes_from_script(script):
    """User ki apni script: har line/paragraph = ek scene. Gemini sirf image prompts banata hai."""
    lines = [l.strip() for l in script.split("\n") if l.strip()][:15]
    try:
        client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        r = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=("For each line below write one vivid English image prompt (no text in image). "
                      "Return ONLY a JSON list of strings, same length and order.\n"
                      + json.dumps(lines, ensure_ascii=False)),
            config={"response_mime_type": "application/json"})
        prompts = json.loads(r.text)
        if len(prompts) != len(lines):
            raise ValueError("length mismatch")
    except Exception:
        prompts = lines  # fallback: line ko hi prompt bana do
    return [{"narration": l, "image_prompt": p} for l, p in zip(lines, prompts)]


def make_image(prompt, path):
    url = ("https://image.pollinations.ai/prompt/" + urllib.parse.quote(prompt)
           + f"?width={W}&height={H}&nologo=true")
    try:
        r = requests.get(url, timeout=90)
        r.raise_for_status()
        with open(path, "wb") as f:
            f.write(r.content)
        Image.open(path).verify()
    except Exception:  # fallback: gradient background
        img = Image.new("RGB", (W, H))
        d = ImageDraw.Draw(img)
        for y in range(H):
            d.line([(0, y), (W, y)], fill=(30 + y // 12, 20, 90 + y // 14))
        img.save(path)


def add_caption(path, text):
    img = Image.open(path).convert("RGB").resize((W, H))
    d = ImageDraw.Draw(img, "RGBA")
    font = None
    for f in [_get_font() or "", "NotoSansDevanagari-Regular.ttf", "Nirmala.ttf", "mangal.ttf",
              "/usr/share/fonts/truetype/noto/NotoSansDevanagari-Regular.ttf", "DejaVuSans.ttf"]:
        try:
            font = ImageFont.truetype(f, 38); break
        except Exception:
            continue
    font = font or ImageFont.load_default()
    lines = textwrap.wrap(text, width=26)
    lh = 52
    top = H - 120 - lh * len(lines)
    d.rectangle([20, top - 15, W - 20, top + lh * len(lines) + 10], fill=(0, 0, 0, 150))
    for i, line in enumerate(lines):
        d.text((40, top + i * lh), line, font=font, fill="white")
    img.save(path)


async def make_voice(text, voice, path):
    await edge_tts.Communicate(text, voice).save(path)


def build_video(topic, n_scenes, language, voice, captions, status, music_path=None, own_script=None):
    tmp = tempfile.mkdtemp()
    if own_script:
        status.write("📝 Aapki script ke scenes bana raha hai...")
        scenes = scenes_from_script(own_script)
    else:
        status.write("📝 Gemini script bana raha hai...")
        scenes = get_scenes(topic, n_scenes, language)
    clips = []
    for i, s in enumerate(scenes):
        status.write(f"🎬 Scene {i+1}/{len(scenes)} ban raha hai...")
        img, aud = f"{tmp}/{i}.jpg", f"{tmp}/{i}.mp3"
        make_image(s["image_prompt"], img)
        if captions:
            add_caption(img, s["narration"])
        asyncio.run(make_voice(s["narration"], voice, aud))
        audio = AudioFileClip(aud)
        clips.append(ImageClip(img).with_duration(audio.duration + 0.4).with_audio(audio))
    status.write("🎞️ Video jod raha hai...")
    out = f"{tmp}/final.mp4"
    final = concatenate_videoclips(clips, method="compose")
    if music_path:  # background music: loop + low volume (voice ke neeche)
        music = AudioFileClip(music_path).with_effects(
            [AudioLoop(duration=final.duration), MultiplyVolume(0.15)])
        final = final.with_audio(CompositeAudioClip([final.audio, music]))
    final.write_videofile(out, fps=24, codec="libx264", audio_codec="aac", logger=None)
    return out


# ---------------- UI ----------------
st.set_page_config(page_title="Free AI Video Maker", page_icon="🎬")
st.title("🎬 Free AI Video Maker")
mode = st.radio("Mode", ["✨ Topic do, AI script banayega", "📝 Meri apni script hai"], horizontal=True)
own = mode.startswith("📝")
if own:
    topic = st.text_area("Apni script yahan paste karein (har line = ek scene)", height=220,
                         placeholder="Diwali roshni ka tyohar hai.\nHar ghar mein diye jalte hain.\nAap sabko Diwali ki shubhkamnayein!")
else:
    topic = st.text_area("Video ka topic / text likho", placeholder="Jaise: Diwali par diya jalane ka mahatva")
c1, c2, c3 = st.columns(3)
n_scenes = c1.slider("Scenes", 3, 8, 4, disabled=own)
voice_name = c2.selectbox("Awaaz", list(VOICES))
captions = c3.checkbox("Captions", True)

music_file = st.file_uploader("🎵 Background music (optional, mp3/wav)", type=["mp3", "wav", "m4a"])

if st.button("🚀 Video banao", type="primary") and topic.strip():
    if "GEMINI_API_KEY" not in os.environ:
        st.error("GEMINI_API_KEY set nahi hai. aistudio.google.com se free key lein.")
    else:
        status = st.status("Shuru ho raha hai...", expanded=True)
        try:
            lang = "Hindi (Devanagari)" if "Hindi" in voice_name else "English"
            music_path = None
            if music_file:
                music_path = os.path.join(tempfile.mkdtemp(), music_file.name)
                with open(music_path, "wb") as f:
                    f.write(music_file.getbuffer())
            path = build_video(topic, n_scenes, lang, VOICES[voice_name], captions, status, music_path,
                               own_script=topic if own else None)
            status.update(label="Video ready! ✅", state="complete")
            st.video(path)
            with open(path, "rb") as f:
                st.download_button("⬇️ Download MP4", f, "video.mp4", "video/mp4")
        except Exception as e:
            status.update(label="Error aaya", state="error")
            st.exception(e)
