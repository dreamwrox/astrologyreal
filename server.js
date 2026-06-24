// ============================================================
//  AstroBot backend (production)
//  Postgres-backed wallet + phone OTP login + Razorpay + bonuses
//  Run: npm install && psql "$DATABASE_URL" -f schema.sql && node server.js
//  Secrets via env — never ship these to the frontend.
// ============================================================

const express = require("express");
const cors = require("cors");
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const jwt = require("jsonwebtoken");
const Razorpay = require("razorpay");
const db = require("./db");

// Load the BNN method reference once at startup. Injected into every reading
// server-side so the BNN logic is authoritative and can't be bypassed by the client.
let BNN_RULES = "";
try {
  BNN_RULES = fs.readFileSync(path.join(__dirname, "bnn_rules.md"), "utf8");
} catch (e) {
  console.warn("bnn_rules.md not found — readings will be generic. Place it next to server.js.");
}

const {
  RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET,
  ANTHROPIC_API_KEY, JWT_SECRET,
  PORT = 4000,
} = process.env;

const app = express();
app.use(cors());                 // lock to your frontend origin in production
app.use(express.json());

const razorpay = new Razorpay({ key_id: RAZORPAY_KEY_ID, key_secret: RAZORPAY_KEY_SECRET });

// ---- auth middleware ----
function auth(req, res, next) {
  const token = (req.headers.authorization || "").replace("Bearer ", "");
  try { req.user = jwt.verify(token, JWT_SECRET); next(); }
  catch { res.status(401).json({ error: "Not logged in" }); }
}

// ============ AUTH: phone OTP ============
// 1) Request a code. In production, send it via an SMS provider (MSG91, Twilio).
app.post("/auth/request-otp", async (req, res) => {
  const { phone } = req.body;
  if (!phone || phone.length < 8) return res.status(400).json({ error: "Valid phone required" });
  const code = ("" + Math.floor(100000 + Math.random() * 900000)); // 6 digits
  await db.setOtp(phone, code);
  // TODO: integrate SMS here. For dev we return it so you can test end-to-end.
  const devEcho = process.env.NODE_ENV === "production" ? undefined : code;
  res.json({ ok: true, devCode: devEcho });
});

// 2) Verify the code -> create/find user -> return a session token.
app.post("/auth/verify-otp", async (req, res) => {
  const { phone, code } = req.body;
  const v = await db.verifyOtp(phone, code);
  if (!v.ok) return res.status(400).json(v);
  const user = await db.findOrCreateUser(phone);
  const token = jwt.sign({ id: user.id, phone }, JWT_SECRET, { expiresIn: "30d" });
  res.json({ ok: true, token, user });
});

// ============ PROFILE ============
app.post("/profile", auth, async (req, res) => {
  const user = await db.saveProfile(req.user.id, req.body);
  res.json({ ok: true, user });
});

// ============ SESSION / STREAK ============
// Call on app open: bumps the daily streak and grants streak bonuses.
app.post("/session/open", auth, async (req, res) => {
  const r = await db.touchStreak(req.user.id);
  res.json(r); // { streak, bonus, balance }
});

app.get("/balance", auth, async (req, res) => {
  res.json({ balancePaise: await db.getBalance(req.user.id) });
});

// ============ COUPONS ============
app.post("/coupon/redeem", auth, async (req, res) => {
  const r = await db.redeemCoupon(req.user.id, req.body.code);
  res.status(r.ok ? 200 : 400).json(r);
});

// ============ RAZORPAY ============
app.post("/create-order", auth, async (req, res) => {
  try {
    const { amount } = req.body;
    if (!amount || amount < 100) return res.status(400).json({ error: "Minimum recharge is 100" });
    const order = await razorpay.orders.create({
      amount: amount * 100, currency: "INR", receipt: `r_${Date.now()}`,
    });
    await db.createOrderRow(order.id, req.user.id, amount);
    res.json({ id: order.id, amount: order.amount, keyId: RAZORPAY_KEY_ID });
  } catch (e) {
    console.error(e); res.status(500).json({ error: "Could not create order" });
  }
});

app.post("/verify", auth, async (req, res) => {
  const { razorpay_order_id, razorpay_payment_id, razorpay_signature } = req.body;
  const expected = crypto.createHmac("sha256", RAZORPAY_KEY_SECRET)
    .update(`${razorpay_order_id}|${razorpay_payment_id}`).digest("hex");
  if (expected !== razorpay_signature) return res.status(400).json({ ok: false, error: "Signature mismatch" });
  try {
    const r = await db.creditOrder(razorpay_order_id); // also applies first-recharge bonus
    res.json({ ok: true, ...r });
  } catch (e) {
    res.status(400).json({ ok: false, error: "Could not credit order" });
  }
});

// ============ PREDICTIONS ============
// Charges the wallet, calls Claude server-side, logs the Q&A. One endpoint = atomic.
app.post("/predict", auth, async (req, res) => {
  try {
    const { system, question, free } = req.body;

    // Free daily horoscope: no charge.
    if (free) {
      const text = await callClaude(system, question);
      return res.json({ text });
    }

    // Paid question: charge first (fails with 402 if short), then answer.
    let charge;
    try {
      charge = await db.chargeAndLogQuestion(req.user.id, question, null, 10);
    } catch (e) {
      return res.status(402).json({ error: "Insufficient balance" });
    }
    const text = await callClaude(system, question);
    // store the answer on the logged row
    await db.pool.query("UPDATE questions SET answer=$2 WHERE id=$1", [charge.questionId, text]);
    res.json({ text, balancePaise: charge.balance });
  } catch (e) {
    console.error(e); res.status(500).json({ error: "Prediction failed" });
  }
});

async function callClaude(system, question) {
  // Prepend the BNN method + reference so every reading follows the book's logic,
  // regardless of what system prompt the client sent.
  const bnnHeader =
    "You are AstroBot, an astrologer who predicts using the Bhrigu Nandi Nadi (BNN) method. " +
    "Follow the BNN reference below exactly. Core method: Jupiter is the self; do NOT use Lagna " +
    "or Dasha; planets combine via same-sign, trinal rashis (1-5-9, 2-6-10, 3-7-11, 4-8-12), " +
    "next-house, or 7th-house (not for Rahu/Ketu); predict by JOINING the karakas of combined " +
    "planets and stating the joining logic plainly. Never declare extreme events (divorce, serious " +
    "illness) as certain — one combination is not the whole chart. Frame everything as BNN guidance.\n\n" +
    "=== BNN REFERENCE ===\n" + BNN_RULES + "\n=== END REFERENCE ===\n\n";
  const fullSystem = bnnHeader + (system || "");

  const r = await fetch("https://api.anthropic.com/v1/messages", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "x-api-key": ANTHROPIC_API_KEY,
      "anthropic-version": "2023-06-01",
    },
    body: JSON.stringify({
      model: "claude-sonnet-4-6", max_tokens: 1000, system: fullSystem,
      messages: [{ role: "user", content: question }],
    }),
  });
  const data = await r.json();
  return (data.content || []).filter((b) => b.type === "text").map((b) => b.text).join("\n").trim();
}

app.listen(PORT, () => console.log(`AstroBot backend on :${PORT}`));
