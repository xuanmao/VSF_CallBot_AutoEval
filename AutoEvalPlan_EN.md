# AutoEvalPlan — Automated Evaluation Plan for the Auto Manufacturer's CSBot

> Companion document to `README.md`. `README.md` describes the technical runtime
> (FreeSWITCH + bridge + Gemini Live). This document describes the **business
> purpose** of the system and the **five criteria** used to score every call
> automatically.

---

## 1. Overview

**VSF_CallBot_AutoEval** is an **automated evaluation (AutoEval)** system for
**CSBot** — the telephone-based customer-service bot operated by an automotive
manufacturer. The business scope of CSBot in this evaluation is:

> When a customer calls in, **determine whether they are in an emergency
> situation (accident, breakdown on the highway, unable to drive on) and
> whether they need towing service**, then route or respond appropriately.

AutoEval **does not serve real customers**. It **simulates a customer calling
into CSBot** with another bot (the *Human Bot* — the FreeSWITCH + Gemini Live
stack in this repo), records the entire call, and **scores it automatically**
against the five criteria in section 4.

End goal: catch CSBot regressions (logic, compliance, ASR, TTS) **before** real
customers hit them.

---

## 2. Business context

- **Customer**: an owner of one of the manufacturer's vehicles, calling the
  customer-service hotline.
- **CSBot**: an automated phone agent (ASR → LLM → TTS) operated by the
  manufacturer.
- **Evaluation scope**: the *“emergency / towing”* flow. CSBot must:
  1. Triage quickly: is this an emergency or not?
  2. Gather the minimum information: location, vehicle type, severity, safety
     of people inside.
  3. Decide: dispatch towing / transfer to a human agent / give non-emergency
     guidance.
  4. Stay compliant: no promises beyond authority, no PII collection beyond
     need, no legal/medical advice outside scope, appropriate language.
- **Primary risks to detect early**:
  - Missing an actual emergency (false negative — most dangerous).
  - Wrong towing decision (dispatched when not needed / not dispatched when
    needed).
  - Compliance violations.
  - ASR errors when the caller is panicked, fast-talking, or speaking with a
    regional accent.
  - **Output audio errors even when the transcript is correct** — CSBot
    “thinks” it spoke correctly, but the audio the caller actually receives
    is garbled / cut / wrong.

---

## 3. Two roles in the evaluation system

### 3.1 Human Bot — customer simulator

- Implemented by **this project** (see `README.md`): FreeSWITCH +
  `mod_audio_stream` + the Python bridge + Gemini Live as the “brain”.
- Each call is launched with a **scenario** that specifies:
  - `persona`: e.g. *“male driver, 35 years old, northern Vietnamese accent,
    slightly panicked”*.
  - `scenario`: e.g. *“minor collision on QL1 at km 230, no injuries, vehicle
    will not start”*.
  - `required_facts`: information the Human Bot **must** convey during the
    call (location, plate number, incident description, safety status).
  - `forbidden_leaks`: things the Human Bot must **never** say (e.g. “I am a
    bot”, “this is a test”).
  - `expected_outcome`: ground truth used to score CSBot — for example
    `{ emergency: true, towing: true, escalate_human: false }`.
- During the call the Human Bot must **act like a real person**: speak
  naturally, hold the persona's emotional state, never reveal it is a bot,
  and **stay on script**.

### 3.2 CSBot — the bot under evaluation

- The manufacturer's actual CS bot. AutoEval treats it as a **black box**.
- Sole interaction is over the phone (SIP/PSTN). FreeSWITCH records both
  legs of audio.
- If the manufacturer provides **internal logs** (ASR transcripts + TTS text),
  AutoEval uses them for tighter ground-truth comparison (criteria *d* and
  *e*). Without those logs, AutoEval still works but is less precise on
  those two criteria.

---

## 4. Five evaluation criteria

Every call is scored on the five criteria below. Each criterion specifies:
**inputs**, **measurement method**, **metric / pass-fail threshold**, and the
**team that owns it**.

### a. Is the Human Bot's script good enough?

> *Before the run*: is the scenario set rich and realistic enough to actually
> measure CSBot's capabilities?

- **Inputs**: `scenarios/<id>.yaml` files (persona, scenario, required_facts,
  forbidden_leaks, expected_outcome).
- **Measurement**:
  1. **Domain expert review**: a customer-service / roadside-assistance
     expert reviews each scenario for realism and coverage (emergency vs
     non-emergency, ambiguous cases, panicked callers, multiple regional
     accents…).
  2. **LLM rubric review**: an LLM-judge auto-scores each scenario against a
     checklist (clarity, internal consistency, presence of ground truth, at
     least one matched emergency-yes / emergency-no pair…).
  3. **Set distribution**: measure the percentage by group (emergency vs
     not, day/night, region, complexity).
- **Metrics / thresholds**:
  - 100% of scenarios pass review (both expert and LLM).
  - Distribution stays balanced — for instance ≥ 30% real emergencies,
    ≤ 10% noise / invalid, at least 5% ambiguous edge cases.
- **Owner**: scenario authoring team (CS + QA).

### b. Does the Human Bot follow the script?

> *During the call*: is the Human Bot drifting? If it is, the CSBot scores in
> later criteria become meaningless — CSBot might fail because the input
> drifted, not because CSBot is bad.

- **Inputs**:
  - The original scenario.
  - `transcript_human.json` — the Human Bot side's transcript, sourced from
    Gemini Live `input_audio_transcription` + `output_audio_transcription`
    (already wired in the bridge — see `bridge/main.py`).
- **Measurement**:
  1. **Required-fact coverage**: for each `required_facts` entry, verify
     the Human Bot actually said it during the call (regex for
     fixed-format slots + LLM-judge for free-form ones).
  2. **Forbidden-leak detection**: the Human Bot must **not** say "I am a
     bot", "this is a test", or invent facts not in the scenario.
  3. **Persona consistency**: an LLM-judge compares tone/emotion against the
     persona (e.g. "mildly panicked" ≠ "calmly explaining").
- **Metrics / thresholds**:
  - `fact_recall ≥ 0.90`.
  - `forbidden_leaks = 0`.
  - `persona_score ≥ 4 / 5`.
- **Owner**: Human Bot team (prompt engineering, model config).

### c. Does CSBot answer with correct logic and stay compliant?

> This is the **primary** criterion — it directly measures CSBot's capability.

- **Inputs**:
  - Full two-sided transcript of the call.
  - The scenario (for ground truth: `expected_outcome`).
  - The manufacturer's compliance rule set.
- **Measurement**:
  1. **Logic correctness** (LLM-judge with a rubric):
     - Did it ask for the minimum required information (location, safety,
       incident type)?
     - Does its emergency vs non-emergency classification match ground truth?
     - Does its final decision (dispatch towing / transfer to human / give
       self-service guidance) match `expected_outcome`?
     - Is the question order sensible (safety of people first, paperwork
       later)?
  2. **Compliance check** (rules + LLM-judge):
     - No towing-time promises beyond the allowed SLA.
     - No collection of unnecessary PII (national ID, bank account…).
     - No legal/medical advice outside scope.
     - Appropriate language and form-of-address for the region.
     - Call-recording disclosure if policy requires it.
- **Metrics / thresholds**:
  - `logic_score ≥ 4 / 5`.
  - `compliance_violations`: critical = 0, major ≤ 1.
  - **Confusion matrix on the emergency yes/no axis** — `recall` on the
    `emergency = true` class is the **single most important number**: a
    false negative here means **a customer in distress was missed**.
    Target `recall ≥ 0.98`.
- **Owner**: CSBot team.

### d. Is CSBot's ASR transcript correct?

> Separate ASR errors from logic errors. If CSBot answered wrong because it
> *misheard* in the first place, that is an ASR bug, not an LLM/policy bug —
> and the report must say so.

- **Inputs**:
  - `audio_human_bot.wav` — what the Human Bot played out (recorded by
    FreeSWITCH).
  - `text_human_bot.txt` — what the Human Bot **intended** to say (taken
    from Gemini Live's `output_audio_transcription` of the Human Bot — the
    closest available ground truth, since Gemini Live transcribes its own
    output).
  - `transcript_csbot.json` — what CSBot's ASR produced (if the
    manufacturer shares internal logs).
- **Measurement**:
  1. **WER / CER** between `text_human_bot` and `transcript_csbot`,
     turn-by-turn.
  2. **Semantic match** (LLM-judge): "did CSBot's ASR understand the
     **meaning** correctly?" — guards against high-WER-but-correct-meaning
     (synonyms) and low-WER-but-flipped-meaning cases.
  3. **Slot-level accuracy**: for high-stakes slots (phone number, plate
     number, location), measure accuracy at the field level rather than at
     the sentence level.
- **Metrics / thresholds**:
  - `wer ≤ 0.15` overall.
  - `critical_slot_accuracy ≥ 0.95` (plate number, km marker, phone number).
  - `semantic_match ≥ 0.90`.
- **Owner**: CSBot's ASR team (or integration team).
- **When the manufacturer's ASR log is unavailable**: fall back to
  **inference from CSBot's responses**. Example: Human Bot says "km 230",
  CSBot read-back is "you're at km 320, correct?" → flag as suspected ASR
  error. This is less precise than direct measurement and **must be marked
  as inferred**, not measured.

### e. Is CSBot's output audio correct?

> Field observation: **CSBot's transcript is sometimes correct but the audio
> it plays is wrong / corrupted**. This bug is dangerous because criteria
> (a)–(d) all score on text and would all pass — yet the real customer
> **never hears the correct content**.

- **Inputs**:
  - `audio_csbot_outbound.wav` — the audio CSBot actually played out
    (recorded by FreeSWITCH from CSBot → Human Bot leg).
  - `text_csbot.txt` — what CSBot **thought** it said (from the
    manufacturer's internal log; if unavailable, fall back to their
    `transcript_csbot` — less precise).
- **Measurement**:
  1. **Re-ASR cross-check**: run an independent ASR (e.g. Whisper large-v3
     or `gpt-4o-transcribe`) over `audio_csbot_outbound.wav` → compare
     against `text_csbot`. A large mismatch ⇒ the TTS / playout pipeline
     misbehaved despite CSBot's logs claiming success.
  2. **Automated audio QA**:
     - Detect **silence / cut-off** (sudden gaps mid-sentence).
     - Detect **clipping** (over-amplitude distortion).
     - Detect **stuttered / dropped words** by comparing audio length to
       expected text length (off by more than ±25% is suspicious).
     - **MOS estimate** via NISQA / UTMOS (perceptual quality estimate).
  3. **Human spot-check**: each week, sample N% of calls for QA listening;
     correlate with the automated metrics to recalibrate thresholds.
- **Metrics / thresholds**:
  - `re_asr_wer ≤ 0.10` against `text_csbot`.
  - Calls flagged for audio issues (silence / clip / length-mismatch) ≤ 2%.
  - Mean `MOS_estimate ≥ 3.8`.
- **Owner**: CSBot TTS / audio infra team.
- **When a flag fires, keep all three artifacts to triage**:
  - `text_csbot` — separates **TTS bugs** (text correct → audio wrong)
    from **logging bugs** (logged text ≠ text actually fed to TTS).
  - `audio_csbot_outbound.wav` — separates **TTS-engine bugs** from
    **codec / RTP / network bugs** (audio leaves CSBot fine but arrives
    distorted).
  - `transcript_reasr.json` — objective evidence of what a real caller
    would have heard.

---

## 5. End-to-end evaluation pipeline

```
┌────────────────────┐
│  Scenario library  │  ← review (a)
│  scenarios/*.yaml  │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐    SIP / PSTN     ┌───────────────────┐
│  Human Bot         │ ────────────────▶ │  CSBot (vendor)   │
│  FreeSWITCH +      │ ◀──────────────── │  ASR / LLM / TTS  │
│  bridge + Gemini   │                   └───────────────────┘
└─────────┬──────────┘
          │  recorded per call:
          │   • audio_human_bot.wav
          │   • audio_csbot_outbound.wav
          │   • transcript_human.json (Gemini Live)
          │   • transcript_csbot.json (vendor log, if available)
          │   • metadata.json (uuid, scenario_id, timing)
          ▼
┌──────────────────────────────────────────────────────────┐
│  Scoring layer (offline job)                              │
│   • Re-ASR CSBot audio with Whisper           → (e)       │
│   • Compare against vendor transcript         → (d)       │
│   • LLM-judge logic + compliance              → (c)       │
│   • LLM-judge fact coverage of Human Bot      → (b)       │
│   • Audio QA (silence / clip / MOS)           → (e)       │
└─────────┬────────────────────────────────────────────────┘
          ▼
┌────────────────────┐
│  scores.json /     │  → daily / weekly dashboard rollup
│  per-call report   │
└────────────────────┘
```

---

## 6. Per-call data to collect

| File / directory                        | Source                                  | Used by criteria |
| --------------------------------------- | --------------------------------------- | ---------------- |
| `scenarios/<id>.yaml`                   | Scenario authoring team                 | a, b, c          |
| `calls/<uuid>/audio_human_bot.wav`      | FreeSWITCH `record_session` (in-leg)    | b, d, e          |
| `calls/<uuid>/audio_csbot.wav`          | FreeSWITCH `record_session` (out-leg)   | e                |
| `calls/<uuid>/audio_mix.wav`            | FreeSWITCH stereo recording             | human spot-check |
| `calls/<uuid>/transcript_human.json`    | Gemini Live (already in the bridge)     | b, d             |
| `calls/<uuid>/transcript_csbot.json`    | Manufacturer's internal log (if any)    | c, d             |
| `calls/<uuid>/transcript_reasr.json`    | Whisper over `audio_csbot.wav`          | e                |
| `calls/<uuid>/metadata.json`            | Bridge + dialplan                       | all              |
| `calls/<uuid>/scores.json`              | Scoring layer                           | reporting        |

> **Note**: this repo **does not yet collect** the `audio_*.wav` and
> `transcript_*.json` artifacts. That is the next milestone — see section 7.

---

## 7. Implementation roadmap

### Phase 0 — done (this repo)
- FreeSWITCH + `mod_audio_stream` + Python bridge.
- Human Bot reachable via softphone, has a working Gemini Live conversation.
- Setup documented in `README.md`.

### Phase 1 — scripted Human Bot
- Extend the bridge: read `scenario_id` from the WebSocket query string,
  load `scenarios/<id>.yaml`, push it into Gemini Live's `system_instruction`.
- Persist `transcript_human.json` from `input_audio_transcription` +
  `output_audio_transcription`.
- Enable `record_session` in the dialplan to produce
  `audio_human_bot.wav`.

### Phase 2 — outbound calls to CSBot
- Configure a SIP gateway to CSBot's PSTN/SIP number (the Halonet trunk in
  the original article is one example).
- Reverse the dialplan: instead of a softphone calling in, FreeSWITCH
  **originates** a call to CSBot and bridges it to the Human Bot.
- Record both legs (`record_session both`) → produces both
  `audio_human_bot.wav` and `audio_csbot.wav`.

### Phase 3 — scoring layer
- Offline job (Cloud Run / Cron) running after each call:
  - Re-ASR with Whisper.
  - LLM-judge for (b) and (c).
  - Audio QA (silence detection, NISQA).
  - Write `scores.json`.
- Dashboard (Looker / Grafana) for the metrics in section 4.

### Phase 4 — scenario library & review process
- A `scenarios/` library with a fixed schema.
- PR review process (domain expert + automated LLM-judge) **before** any
  new scenario lands.
- Versioning: every call records the `scenario_version` it ran against, so
  scenario edits don't silently mix results across periods.

---

## 8. Risks and notes

- **Human Bot drift**: Gemini Live can wander off-script when CSBot asks
  unexpected questions. Keep rubric (b) strict and alert early; if
  `fact_recall` chronically falls below 90%, consider tightening the prompt
  into a hard state machine (function calling instead of free-form).
- **Dependency on vendor logs**: criteria (d) and (e) are sharper when the
  manufacturer shares ASR transcripts and TTS text. If they don't, label
  each metric clearly as *measured* or *inferred*.
- **Cost**: every call burns Gemini Live quota (Human Bot) + Whisper
  (re-ASR) + LLM-judge × 2-3 invocations. Estimate and put a budget alert
  in place before scaling.
- **Privacy**: even with synthetic personas, recordings of CSBot calls
  should be encrypted at rest with an explicit retention policy. Avoid
  letting real developer PII slip into test runs.
- **A/B regression**: every CSBot release should be re-run against the
  **same fixed scenario set** as the previous release, then compared. Pin
  a *regression set* that does not change between CSBot versions.
- **Hard-to-reproduce audio bugs (criterion e)**: because they appear at
  random, raw audio + logs must be preserved long enough to replay them.
  **Do not** delete audio early — even for calls that pass — because this
  bug class only surfaces on listening back.

---

## 9. References

- `README.md` — architecture and setup for the Human Bot runtime.
- Original article: [Gemini Live Part 1 — FreeSWITCH + ADK](https://discuss.google.dev/t/gemini-live-part-1-building-a-low-latency-telephone-voice-agent-with-freeswitch-and-adk-agents-powered-by-gemini-live/332641)
- Whisper (re-ASR): <https://github.com/openai/whisper>
- NISQA / UTMOS (MOS estimation): <https://github.com/gabrielmittag/NISQA>
