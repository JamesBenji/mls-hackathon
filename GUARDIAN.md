# 🛡️ Guardian

**A private, multimodal clinical voice-agent.** Guardian calls patients after hospital discharge, talks to them in natural voice, can *look* at a wound photo, reasons like a clinician, and escalates to a human when it hears danger — all while running on our own infrastructure so patient data never touches a third-party cloud LLM.

> **One-liner for the pitch:** *"Guardian talks to patients, looks at their wounds, reasons like a clinician, and never sends a byte to a third-party cloud."*

---

## 📋 Table of Contents
1. [The Problem](#-the-problem)
2. [The Solution](#-the-solution)
3. [The Stack](#-the-stack-all-three-sponsors-load-bearing)
4. [Architecture](#-architecture)
5. [Core Flow](#-core-flow)
6. [Feature List](#-feature-list)
7. [MedGemma Upgrade](#-the-medgemma-upgrade-voice--vision)
8. [Agent Features](#-agent-features)
9. [5-Hour Build Plan](#-5-hour-build-plan)
10. [The Demo Script](#-the-demo-script-90-seconds)
11. [Risks & Fallbacks](#-risks--fallbacks)
12. [Decisions Locked](#-decisions-locked)
13. [Repo Structure](#-planned-repo-structure)

---

## 🩺 The Problem
Up to **20% of hospital readmissions are preventable** with a simple follow-up call — a call nobody has time to make. Nurses are stretched thin, and the patients who most need checking in (elderly, non-English-speaking, post-surgery) are the least likely to use an app.

## 💡 The Solution
Guardian is an **autonomous voice agent** that performs post-discharge check-ins:
- Calls/talks to the patient in natural voice
- Asks clinically-informed follow-up questions, adapting to answers
- Can assess a **photo** of a wound/incision/rash (MedGemma vision)
- Detects danger signals and **escalates to a human** with reasoning + evidence
- Logs a clean clinical summary to a care-team dashboard
- **Runs entirely on our own infrastructure** — the core privacy differentiator

---

## 🧰 The Stack (all three sponsors load-bearing)

| Tool | Role | Why it's essential |
|------|------|--------------------|
| **ElevenLabs** | Conversational AI voice (STT + TTS, two-way) | Natural, low-latency voice the patient actually wants to talk to. Multilingual. |
| **MedGemma** (Gemma, medical-tuned) on **DigitalOcean** | The private clinical brain: triage reasoning, vision, summaries | Medical-tuned → trustworthy reasoning + reads medical images. Self-hosted → private. |
| **DigitalOcean** | GPU droplet hosting MedGemma + agent backend + dashboard | Powers the private LLM; the whole "data never leaves" story depends on it. |

**Privacy pitch:** *"The patient's health conversation and photos are understood by MedGemma running on our own DigitalOcean server — their medical data never left our infrastructure."*

---

## 🏗 Architecture

```
┌─────────────┐   voice    ┌──────────────────────┐
│ Browser mic │◄─────────►│ ElevenLabs             │
│ (patient)   │            │ Conversational AI      │
└─────────────┘            │ widget (voice I/O)     │
                           └──────────┬─────────────┘
                                      │ transcript events
                                      ▼
                           ┌──────────────────────┐
         photo upload ────►│ Agent backend (DO)    │
                           │ FastAPI               │
                           │ - agent loop          │
                           │ - risk assessment     │
                           │ - tool calls          │
                           │ - escalate / log      │
                           └──────────┬────────────┘
                                      │
              ┌───────────────────────┼───────────────────────┐
              ▼                       ▼                        ▼
     ┌─────────────────┐   ┌────────────────────┐   ┌──────────────────┐
     │ MedGemma on DO  │   │ Live dashboard      │   │ Escalation action │
     │ GPU droplet     │   │ (care team console) │   │ (alert nurse /    │
     │ vision + text   │   │ transcript, risk,   │   │  "call human")    │
     │ (private LLM)   │   │ image, reasoning    │   └──────────────────┘
     └─────────────────┘   └────────────────────┘
```

---

## 🔄 Core Flow

1. **Check-in starts** → ElevenLabs voice greets patient, asks opening question.
2. **Patient replies by voice** → transcript streamed to backend.
3. **Backend prompts MedGemma** → returns structured JSON (risk, flags, next question, sentiment, summary, escalate).
4. **Agent loops** → asks its own next question until satisfied.
5. **Optional photo** → patient sends wound photo → MedGemma vision assesses → result + image on dashboard.
6. **Danger detected** → ⚠️ ESCALATE fires on dashboard with reasoning + evidence; mock alert "sent" to on-call nurse.
7. **Call ends** → auto-generated SOAP note logged to patient timeline.

**The MedGemma contract (structured output):**
```json
{
  "risk": "none | low | high",
  "flags": ["chest pain", "dizziness"],
  "sentiment": "calm | anxious | distressed",
  "next_question": "the agent's chosen follow-up, or null if done",
  "summary_for_nurse": "one-line English summary",
  "soap_note": "S/O/A/P formatted clinical note",
  "escalate": true
}
```
Escalation triggers: chest pain, difficulty breathing, severe bleeding, confusion, suicidal statements, fainting.

---

## ✨ Feature List

### 🔥 Demo-wow (cheap, high visual payoff)
- **Autonomous follow-up loop** — agent chooses its own next question; dashboard shows "Guardian is deciding…"
- **Live tool-call log** — watch the agent reach for tools: `check_medication_interaction`, `lookup_patient_history`, `book_appointment`
- **Reasoning trace** — a panel narrating *why* it's escalating ("chest pain + dizziness → cardiac risk → escalate")
- **Sentiment / distress meter** — emotional tone bar per turn (pairs with voice: "sounds fine but keeps mentioning pain")
- **Live severity color** — dashboard border shifts green → amber → red with risk
- **Vitals gauge** — patient reports a number ("BP 160/95") → plotted against normal/danger bands
- **"Call a human" bridge** — escalate visibly "dials" a nurse (mocked ringing)
- **🔒 Privacy badge** — *"Processed on-premise · 0 bytes sent to external LLM"*

### 🧠 Depth (makes it feel like a real product)
- **Patient timeline / trend tracking** — check-in history with improving ↑ / worsening ↓ arrows
- **Multi-patient queue** — nurse console with risk badges; high-risk floats to top
- **Red-flag protocol library** — configurable escalation rules per patient type
- **Consent + audit log** — "Patient consented 14:02" + full action trail (reads as *mature* in healthcare)
- **PII redaction view** — names/IDs auto-masked before storage (proves the privacy claim visually)
- **Auto-generated SOAP note** — clean clinical note at call end (tangible artifact for judges)

### 📊 Nice-to-haves
- Export to PDF / "send to EHR" (mocked)
- Multilingual toggle (ElevenLabs supports it)
- Accessibility mode (large text / high contrast — genuinely relevant for elderly patients)

---

## 🚀 The MedGemma Upgrade: Voice + Vision

**MedGemma** (Google's medical-tuned Gemma) is the key unlock. Because it's trained on medical text **and images** (MedGemma 4B is multimodal), Guardian can do things a general model would hallucinate on:

- **🖼️ Medical image understanding (HEADLINE FEATURE)** — patient/caregiver snaps a photo of a wound, incision, rash, or swelling → MedGemma assesses it → finding shown next to the image on the dashboard. **Voice + vision = a dramatically bigger demo than voice alone.**
- **Clinically-grounded triage** — differential suggestions, "these symptoms together suggest X, rule out Y" — safer than base Gemma.
- **Medication reconciliation** — knows drugs; flags interactions/contraindications with real reasoning.
- **Clinical-grade SOAP notes** — writes summaries in proper medical register.
- **Patient medical Q&A** — grounded answers to "is this normal after surgery?"

**Decision:** *Vision with text fallback.* Try MedGemma 4B multimodal; if the GPU/serving fights us, fall back to MedGemma text-only, then base `gemma2`. The image path is built but degrades gracefully.

> ⚠️ **Safety framing (always):** This is clinical **decision support** — a clinician reviews. Never say "diagnosis." Judges and reality both care about this.

---

## 🤖 Agent Features

Guardian is a true agent, not a form, because it has:

| Capability | How it shows on the dashboard |
|------------|-------------------------------|
| **Autonomy** | Chooses its own next question; decides when it has enough info and stops |
| **Tool use** | Live log of tool calls (med-interaction check, history lookup, booking) |
| **Memory** | Remembers prior check-ins ("last week you mentioned a cough — better or worse?") |
| **Goal-seeking** | Keeps probing until risk is assessed; routes to the right action |
| **Multi-path decisions** | Chooses: reassure & close / schedule follow-up / notify nurse / call 911 |
| **Reasoning transparency** | Narrates *why* it took an action |

**Roadmap (mention to judges, don't build in 5h):** multi-agent handoff (Triage → Clinical → Escalation agents), confidence scoring + human-in-the-loop ("I'm 60% sure, routing to a human").

---

## ⏱ 5-Hour Build Plan

| Hour | Focus | Checkpoint |
|------|-------|------------|
| **0–1** | **Infra first.** DO GPU droplet, serve MedGemma via Ollama. Verify text + image requests via curl. Pull base gemma2 on laptop as fallback. | curl returns a MedGemma completion |
| **1–2.5** | **Backend + agent brain.** FastAPI `/chat` + `/image` + WebSocket. Nail the structured-JSON prompt. | POST fake transcript → risk JSON back |
| **2.5–3.5** | **ElevenLabs voice.** Conversational AI widget in browser; hook transcript events to backend. | Speak → natural voice replies |
| **3.5–4.5** | **Dashboard.** Transcript, risk meter, sentiment, image panel, reasoning trace, ESCALATE banner, privacy badge. | Say "chest pain" → banner fires |
| **4.5–5** | **Escalation + polish.** Mock alert, "Replay demo" button (canned transcript through real pipeline), rehearse pitch ×2. | Full run start → escalate works |

**De-risk order:** hardest/scariest first (infra), demo-visible last (dashboard), always keep a working demo at each checkpoint.

---

## 🎤 The Demo Script (90 seconds)

1. **Hook:** *"20% of hospital readmissions are preventable with a follow-up call nobody has time to make."*
2. **Start the call** → calm check-in in natural voice.
3. **Patient:** *"Actually my chest feels tight, and the incision looks a bit red."*
4. **"Can you send a photo?"** → wound photo appears on dashboard; MedGemma: *"erythema around incision, possible early infection."*
5. **Point at the dashboard:** keywords light red, risk → HIGH, sentiment → distressed, **⚠️ ESCALATE** fires with reasoning + image evidence, "📱 alerting Dr. Lee."
6. **Close:** *"The conversation and the photo were understood by MedGemma on our own DigitalOcean server — the patient's medical data never left our infrastructure. And ElevenLabs means it works for any patient, in any language, who'd never use an app."*

---

## ⚠️ Risks & Fallbacks

| Risk | Mitigation |
|------|-----------|
| **MedGemma vision too heavy for the GPU** | Fall back to MedGemma text-only → base gemma2. Keep vision as a roadmap slide. Verify in **hour 0**. |
| **MedGemma not a one-command pull** | It's gated on Hugging Face; check the serving path early. Don't sink the whole hackathon into it. |
| **Droplet dies mid-demo** | `gemma2` pulled on laptop; flip `MEDGEMMA_URL` to localhost. Still truthfully "runs on DigitalOcean." |
| **Live mic fails on stage** | **"Replay demo" button** feeds a canned transcript through the *real* pipeline — nobody can tell. |
| **Scope creep** | Cut ruthlessly: no auth, no real DB, no language picker, no real SMS. Fake what you can — a demo rewards *looking* real. |

---

## ✅ Decisions Locked

- **Idea:** Guardian — post-discharge check-in voice agent (+ Translator framing as a "works in any language" talking point)
- **Voice channel:** Browser mic (safe), via **ElevenLabs Conversational AI widget**
- **Model:** **MedGemma** — **vision with text fallback** — on a **DigitalOcean GPU droplet** (Ollama)
- **Translation:** ElevenLabs handles multilingual voice; MedGemma reasons on transcript
- **Demo language:** **English** (zero live translation risk; "multilingual" is a talking point)
- **Build stack:** **Python (FastAPI) + plain HTML/JS** dashboard
- **Scaffold approach:** vision hooks built in, graceful degradation if unavailable

---

## 📁 Planned Repo Structure

```
mls-hackathon/
├── GUARDIAN.md            # this file
├── backend/
│   ├── main.py            # FastAPI: /chat, /image, WebSocket, agent loop
│   ├── agent.py           # MedGemma prompt + agent loop + tool registry
│   ├── medgemma.py        # client w/ vision → text → base fallback chain
│   └── requirements.txt
├── dashboard/
│   ├── index.html         # care-team console
│   ├── app.js             # WebSocket client, renders everything
│   └── style.css
├── .env.example           # MEDGEMMA_URL, ELEVENLABS_API_KEY, ...
└── README.md              # setup + run instructions
```

---

## 👥 Team Notes

- **Privacy is the thesis.** Every feature should reinforce "private, on-prem, medical-grade." When in doubt, add the 🔒 framing.
- **Keep a working demo at every checkpoint.** Never be in a state where nothing runs.
- **"Decision support, not diagnosis."** Say it in the pitch, put it in the UI.
- **The image feature is the headline.** If one thing has to work live, make it the wound-photo assessment.
