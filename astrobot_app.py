"""
AstroBot — Streamlit MVP
BNN/Vedic astrology chatbot with a coupon wallet (₹100 codes, ₹10 per question),
birth-detail intake, a free daily horoscope, and a cartoon robot mascot.

Run locally:
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-xxx     # or put it in .streamlit/secrets.toml
    streamlit run astrobot_app.py

Deploy: push to GitHub -> share.streamlit.io -> add ANTHROPIC_API_KEY in app secrets.
"""

import os
import datetime
import streamlit as st
import streamlit.components.v1 as components
import anthropic

# ---------------- config ----------------
COST_PER_Q = 10
RECHARGE_MIN = 100
COUPONS = {"ASTRO100": 100, "STARS100": 100, "COSMIC200": 200}
ZODIAC = ["Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
          "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces"]

# Voice + reading languages. Each: display label -> (speech BCP-47 code, language name for Claude)
LANGUAGES = {
    "English":  ("en-IN", "English"),
    "हिन्दी (Hindi)":    ("hi-IN", "Hindi"),
    "ਪੰਜਾਬੀ (Punjabi)":  ("pa-IN", "Punjabi"),
    "বাংলা (Bangla)":    ("bn-IN", "Bengali"),
    "தமிழ் (Tamil)":     ("ta-IN", "Tamil"),
    "ಕನ್ನಡ (Kannada)":   ("kn-IN", "Kannada"),
}


# ---------------- zodiac sign detection ----------------
# Western (tropical) sun sign from birth DATE alone. Instant, no time/place needed.
# (end_day, sign) — a date on/before end_day in that month belongs to the given sign.
_WESTERN = [
    (19, "Capricorn"), (18, "Aquarius"), (20, "Pisces"), (19, "Aries"),
    (20, "Taurus"), (20, "Gemini"), (22, "Cancer"), (22, "Leo"),
    (22, "Virgo"), (22, "Libra"), (21, "Scorpio"), (21, "Sagittarius"),
]

def western_sign(d):
    """Return the Western sun sign for a datetime.date."""
    if d is None:
        return None
    end_day, sign = _WESTERN[d.month - 1]
    if d.day > end_day:
        # rolls into the next sign; December rolls to Capricorn
        return _WESTERN[d.month % 12][1]
    return sign


def vedic_rashi(d, t=None, place=None):
    """
    Proper Vedic (sidereal) rashi requires birth date, time, place and an astronomy
    library to compute the Moon/Sun longitude minus ayanamsa. Not yet wired in.

    UPGRADE: install a library and compute here, e.g.
        pip install vedicastro   (or pyswisseph + a geocoder)
    then return the sidereal sign. Until then we return None so the app falls back
    to the Western sign and labels it accordingly.
    """
    return None


def detect_sign(d, t=None, place=None):
    """Use Vedic rashi if available, else Western. Returns (sign, system_label)."""
    v = vedic_rashi(d, t, place)
    if v:
        return v, "Vedic"
    w = western_sign(d)
    return w, "Western"


# ---------------- voice assistant (browser Web Speech API) ----------------
def speak_html(text, lang_code="en-IN"):
    """Speak the given text aloud using the browser's speech synthesis in the chosen language."""
    return f"""
    <script>
      (function() {{
        try {{
          const msg = new SpeechSynthesisUtterance("{text}");
          msg.rate = 1.0; msg.pitch = 1.0; msg.lang = "{lang_code}";
          // Prefer a voice matching the language if the browser has one.
          const pick = () => {{
            const vs = window.speechSynthesis.getVoices();
            const m = vs.find(v => v.lang === "{lang_code}") || vs.find(v => v.lang && v.lang.slice(0,2) === "{lang_code}".slice(0,2));
            if (m) msg.voice = m;
            window.speechSynthesis.cancel();
            window.speechSynthesis.speak(msg);
          }};
          if (window.speechSynthesis.getVoices().length) pick();
          else window.speechSynthesis.onvoiceschanged = pick;
        }} catch (e) {{}}
      }})();
    </script>
    """

# Microphone capture in the chosen language; shows recognized text to copy into chat.
def voice_input_html(lang_code="en-IN"):
    return """
<div style="font-family: system-ui, sans-serif;">
  <button id="mic" style="background:#6D28D9;color:#fff;border:none;border-radius:10px;
    padding:10px 16px;font-weight:600;cursor:pointer;">\U0001F3A4 Start speaking</button>
  <p id="out" style="color:#EDE9FE;margin-top:10px;min-height:20px;"></p>
  <script>
    (function(){
      const LANG = "%LANG%";
      const btn = document.getElementById('mic');
      const out = document.getElementById('out');
      const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
      if (!SR) { out.textContent = "Voice input not supported in this browser. Try Chrome."; btn.disabled = true; return; }
      const rec = new SR();
      rec.lang = LANG; rec.interimResults = true; rec.continuous = false;
      let finalText = "";
      btn.onclick = function(){
        finalText = ""; out.textContent = "Listening\\u2026";
        try { rec.start(); } catch(e) {}
      };
      rec.onresult = function(e){
        let interim = "";
        for (let i = e.resultIndex; i < e.results.length; i++){
          const t = e.results[i][0].transcript;
          if (e.results[i].isFinal) finalText += t; else interim += t;
        }
        out.textContent = (finalText + " " + interim).trim();
      };
      rec.onerror = function(){ out.textContent = "Didn't catch that \\u2014 try again."; };
    })();
  </script>
</div>
""".replace("%LANG%", lang_code)


st.set_page_config(page_title="AstroBot", page_icon="\u2728", layout="centered")

# ---------------- BNN knowledge base ----------------
# Loads the BNN method reference so every reading follows the book's logic.
def load_bnn_rules():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, "bnn_rules.md")
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except Exception:
        return ""

BNN_RULES = load_bnn_rules()

# ---------------- api key ----------------
def get_api_key():
    key = os.environ.get("ANTHROPIC_API_KEY")
    if not key:
        try:
            key = st.secrets["ANTHROPIC_API_KEY"]
        except Exception:
            key = None
    return key

API_KEY = get_api_key()

# ---------------- session state ----------------
def init_state():
    defaults = {
        "wallet": 0,
        "redeemed": set(),
        "unlocked": False,        # app stays gated until a valid coupon is redeemed
        "profile_set": False,
        "birth": {"name": "", "date": None, "time": "", "place": "", "sign": "Aries"},
        "messages": [],
        "horoscope": "",
        "voice_on": True,         # read bot answers aloud
        "language": "English",    # voice + reading language
    }
    for k, v in defaults.items():
        if k not in st.session_state:
            st.session_state[k] = v

init_state()

# ---------------- styling + mascot ----------------
st.markdown("""
<style>
  .stApp { background: radial-gradient(1200px 500px at 50% -120px, #161243, #0B0A1F); }
  .block-container { max-width: 720px; }
  h1, h2, h3, p, label, span, div { color: #EDE9FE; }
  .wallet { background:#1E1A4D; border:1px solid #6D28D9; border-radius:14px;
            padding:8px 16px; display:inline-block; }
  .wallet b { color:#F5C518; font-size:22px; }
  .botmsg { background:#161243; border:1px solid #6D28D955; border-radius:14px 14px 14px 4px;
            padding:12px 14px; margin:6px 0; }
  .usermsg { background:#6D28D9; border-radius:14px 14px 4px 14px;
             padding:12px 14px; margin:6px 0; text-align:right; }
  .foot { color:#C7C3E8; font-size:12px; text-align:center; opacity:.8; margin-top:24px; }
</style>
""", unsafe_allow_html=True)

ROBOT_SVG = """
<div style="text-align:center;">
<svg viewBox="0 0 160 150" width="120" height="115">
  <defs>
    <radialGradient id="halo" cx="50%" cy="40%" r="60%">
      <stop offset="0%" stop-color="#A78BFA" stop-opacity="0.9"/>
      <stop offset="100%" stop-color="#6D28D9" stop-opacity="0"/></radialGradient>
    <linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0%" stop-color="#EDE9FE"/><stop offset="100%" stop-color="#B9AEF0"/></linearGradient>
  </defs>
  <circle cx="80" cy="70" r="66" fill="url(#halo)">
    <animate attributeName="r" values="60;70;60" dur="3s" repeatCount="indefinite"/></circle>
  <line x1="80" y1="30" x2="80" y2="16" stroke="#F5C518" stroke-width="3"/>
  <text x="80" y="16" text-anchor="middle" font-size="15" fill="#F5C518">&#10022;
    <animateTransform attributeName="transform" type="rotate" from="0 80 12" to="360 80 12" dur="6s" repeatCount="indefinite"/></text>
  <rect x="42" y="32" width="76" height="60" rx="18" fill="url(#body)" stroke="#6D28D9" stroke-width="2"/>
  <rect x="50" y="44" width="60" height="34" rx="14" fill="#0B0A1F"/>
  <circle cx="68" cy="61" r="5" fill="#F5C518">
    <animate attributeName="r" values="5;1.5;5" dur="4s" repeatCount="indefinite"/></circle>
  <circle cx="92" cy="61" r="5" fill="#F5C518">
    <animate attributeName="r" values="5;1.5;5" dur="4s" repeatCount="indefinite"/></circle>
  <rect x="72" y="70" width="16" height="3" rx="1.5" fill="#A78BFA"/>
  <rect x="52" y="92" width="56" height="44" rx="14" fill="url(#body)" stroke="#6D28D9" stroke-width="2"/>
  <circle cx="80" cy="112" r="8" fill="#161243"/>
  <circle cx="80" cy="112" r="4" fill="#F5C518">
    <animate attributeName="opacity" values="1;0.3;1" dur="2s" repeatCount="indefinite"/></circle>
</svg>
</div>
"""

# ---------------- claude ----------------
def call_claude(system, user_text):
    if not API_KEY:
        return "(Set ANTHROPIC_API_KEY to enable live readings.)"
    try:
        client = anthropic.Anthropic(api_key=API_KEY)
        resp = client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1000,
            system=system,
            messages=[{"role": "user", "content": user_text}],
        )
        return "".join(b.text for b in resp.content if b.type == "text").strip()
    except Exception as e:
        return f"The cosmic link dropped: {e}"

def reading_system():
    b = st.session_state.birth
    return (
        "You are AstroBot, an astrologer who predicts using the Bhrigu Nandi Nadi (BNN) "
        "method. Follow the BNN reference below exactly. Core method: Jupiter is the self; "
        "do NOT use Lagna or Dasha; planets come into combination via same-sign, trinal "
        "rashis (1-5-9, 2-6-10, 3-7-11, 4-8-12), next-house, or 7th-house (not for Rahu/Ketu); "
        "and you make predictions by JOINING the karakas of combined planets, stating the "
        "joining logic plainly (e.g. 'Saturn is career and Moon is change, so...').\n\n"
        "=== BNN REFERENCE ===\n" + BNN_RULES + "\n=== END REFERENCE ===\n\n"
        f"Querent: name {b['name']}, date {b['date']}, time {b['time'] or 'unknown'}, "
        f"place {b['place'] or 'unknown'}, sign {b['sign']}.\n"
        "If the querent has not provided their planetary placements (which rashi each planet "
        "sits in), briefly ask for the key ones relevant to their question, OR give an "
        "indicative reading based on what they've shared and say so. Keep answers to 4-6 warm "
        "sentences. Never declare extreme events (divorce, serious illness) as certain — note "
        "that one combination is not the whole chart. Frame everything as BNN guidance. "
        f"IMPORTANT: Write your entire reply in {LANGUAGES[st.session_state.language][1]}. "
        "Use natural, conversational phrasing in that language."
    )

# ---------------- header ----------------
st.markdown(ROBOT_SVG, unsafe_allow_html=True)
st.markdown("<h1 style='text-align:center;margin:0;'>AstroBot</h1>", unsafe_allow_html=True)
st.markdown(
    "<p style='text-align:center;color:#C7C3E8;margin-top:2px;'>BNN &amp; Vedic guidance \u00b7 your pocket sky-reader</p>",
    unsafe_allow_html=True,
)
st.markdown(
    f"<div style='text-align:center;margin:8px 0;'><span class='wallet'>Wallet &nbsp;<b>\u20B9{st.session_state.wallet}</b></span></div>",
    unsafe_allow_html=True,
)

# ---------------- coupon gate ----------------
# The app stays locked until the user redeems a valid coupon. Everything below
# (birth details, horoscope, chat) only renders once unlocked.
if not st.session_state.unlocked:
    with st.container(border=True):
        st.subheader("Enter your coupon to begin")
        st.caption(
            f"AstroBot is coupon-based. Redeem a code (min \u20B9{RECHARGE_MIN}) to unlock. "
            f"Each question then costs \u20B9{COST_PER_Q}."
        )
        g1, g2 = st.columns([3, 1])
        gate_code = g1.text_input(
            "Coupon code", label_visibility="collapsed",
            placeholder="Enter coupon code", key="gate_code",
        )
        if g2.button("Unlock", use_container_width=True, type="primary"):
            c = gate_code.strip().upper()
            if c in COUPONS:
                st.session_state.wallet += COUPONS[c]
                st.session_state.redeemed.add(c)
                st.session_state.unlocked = True
                st.rerun()
            else:
                st.error("That coupon code isn't valid.")
    st.markdown(
        "<p class='foot'>Guidance is based on Vedic/BNN principles and is for reflection, not certainty.</p>",
        unsafe_allow_html=True,
    )
    st.stop()   # nothing below renders until unlocked

# ---------------- recharge (coupon only) ----------------
with st.container(border=True):
    st.subheader("Recharge wallet")
    st.caption(f"Min \u20B9{RECHARGE_MIN} via coupon. Each question costs \u20B9{COST_PER_Q}.")
    c1, c2 = st.columns([3, 1])
    code = c1.text_input("Coupon code", label_visibility="collapsed", placeholder="Enter coupon code")
    if c2.button("Redeem", use_container_width=True):
        c = code.strip().upper()
        if c in st.session_state.redeemed:
            st.warning("That coupon is already used.")
        elif c in COUPONS:
            st.session_state.wallet += COUPONS[c]
            st.session_state.redeemed.add(c)
            st.success(f"Added \u20B9{COUPONS[c]} to your wallet.")
        else:
            st.error("That coupon code isn't valid.")

# ---------------- profile ----------------
if not st.session_state.profile_set:
    with st.container(border=True):
        st.subheader("Your birth details")
        b = st.session_state.birth
        col1, col2 = st.columns(2)
        b["name"] = col1.text_input("Name", b["name"])
        b["date"] = col2.date_input(
            "Birth date",
            value=None,
            min_value=datetime.date(1900, 1, 1),
            max_value=datetime.date.today(),
        )
        b["time"] = col1.text_input("Birth time (e.g. 14:30)", b["time"])
        b["place"] = col2.text_input("Birth place", b["place"])

        # Auto-detect the sign from the birth date (live preview before saving).
        detected, system_label = detect_sign(b["date"], b["time"], b["place"])
        if detected:
            st.info(f"\u2728 Your sign is **{detected}** ({system_label}).")
        else:
            st.caption("Pick your birth date and I'll detect your sign.")

        if st.button("Align my chart", type="primary", use_container_width=True):
            if not b["name"] or not b["date"]:
                st.warning("Add at least your name and birth date.")
            else:
                b["sign"] = detected or "Aries"
                st.session_state.profile_set = True
                st.session_state.messages = [
                    ("bot", f"Namaste {b['name']}. Your sign is {b['sign']}. "
                            f"I've aligned to your chart. Each question draws \u20B9{COST_PER_Q}.")
                ]
                st.rerun()

# ---------------- daily horoscope ----------------
if st.session_state.profile_set:
    with st.container(border=True):
        hc1, hc2 = st.columns([3, 1])
        hc1.subheader(f"Today for {st.session_state.birth['sign']}")
        if hc2.button("Reveal (free)", use_container_width=True):
            sys = (f"You are AstroBot. Give a short, upbeat daily horoscope (2-3 sentences) for the "
                   f"{st.session_state.birth['sign']} sign, grounded in Vedic transit logic. "
                   f"Mention one practical focus for today. Never claim certainty. "
                   f"Write the entire reply in {LANGUAGES[st.session_state.language][1]}.")
            with st.spinner("Consulting the transits\u2026"):
                st.session_state.horoscope = call_claude(sys, f"Reading for {st.session_state.birth['sign']}.")
        st.write(st.session_state.horoscope or "Tap reveal for your free daily reading.")

# ---------------- chat ----------------
if st.session_state.profile_set:
    with st.container(border=True):
        # Voice controls
        v1, v2, v3 = st.columns([1.2, 1, 1.4])
        st.session_state.language = v1.selectbox(
            "Language", list(LANGUAGES.keys()),
            index=list(LANGUAGES.keys()).index(st.session_state.language),
            label_visibility="collapsed",
        )
        lang_code = LANGUAGES[st.session_state.language][0]
        st.session_state.voice_on = v2.toggle("\U0001F50A Read aloud", value=st.session_state.voice_on)

        # Voice input in the chosen language.
        with v3.expander("\U0001F3A4 Speak"):
            components.html(voice_input_html(lang_code), height=140)
            st.caption("Speak, copy the captured text, and paste into the box below to send.")

        for role, text in st.session_state.messages:
            css = "usermsg" if role == "user" else "botmsg"
            st.markdown(f"<div class='{css}'>{text}</div>", unsafe_allow_html=True)

        q = st.chat_input(
            f"Ask about career, love, timing\u2026 (\u20B9{COST_PER_Q})"
            if st.session_state.wallet >= COST_PER_Q else "Recharge to ask\u2026"
        )
        if q:
            if st.session_state.wallet < COST_PER_Q:
                st.warning(f"Need \u20B9{COST_PER_Q}. Recharge above (min \u20B9{RECHARGE_MIN}).")
            else:
                st.session_state.wallet -= COST_PER_Q
                st.session_state.messages.append(("user", q))
                with st.spinner("Reading the stars\u2026"):
                    answer = call_claude(reading_system(), q)
                st.session_state.messages.append(("bot", answer))
                st.rerun()

        # Read the latest bot reply aloud (browser speech synthesis).
        if st.session_state.voice_on and st.session_state.messages:
            last_role, last_text = st.session_state.messages[-1]
            if last_role == "bot":
                safe = (last_text or "").replace("\\", " ").replace("`", " ").replace('"', " ").replace("\n", " ")
                components.html(speak_html(safe, lang_code), height=0)

st.markdown(
    "<p class='foot'>Guidance is based on Vedic/BNN principles and is for reflection, not certainty.</p>",
    unsafe_allow_html=True,
)
