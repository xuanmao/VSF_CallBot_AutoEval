# AutoEvalPlan — Kế hoạch đánh giá tự động cho CSBot của hãng xe ô tô

> Tài liệu đi kèm với `README.md`. `README.md` mô tả runtime kỹ thuật (FreeSWITCH +
> bridge + Gemini Live). Tài liệu này mô tả **mục đích nghiệp vụ** của hệ thống và
> **năm tiêu chí** dùng để chấm điểm tự động mỗi cuộc gọi.

---

## 1. Tổng quan

Dự án **VSF_CallBot_AutoEval** là một hệ thống **đánh giá tự động (AutoEval)** dành cho
**CSBot** — bot CSKH qua điện thoại của một hãng sản xuất ô tô. Nhiệm vụ nghiệp vụ
của CSBot trong phạm vi đánh giá này là:

> Khi khách hàng gọi vào, **xác định xem khách có đang trong tình huống khẩn cấp
> (tai nạn, hỏng xe trên cao tốc, không thể tự di chuyển…) và có cần dịch vụ
> cứu hộ / kéo xe hay không**, sau đó đưa ra phản hồi/điều phối phù hợp.

AutoEval **không phục vụ khách hàng thật**. Nó **mô phỏng khách hàng gọi vào CSBot**
bằng một bot khác (gọi là *Human Bot* — chính là hệ thống FreeSWITCH + Gemini Live
trong repo này), thu lại toàn bộ cuộc gọi, rồi **chấm điểm tự động** theo 5 tiêu chí
trình bày ở mục 4.

Mục tiêu cuối cùng: phát hiện sớm regression của CSBot (logic, compliance, ASR, TTS)
trước khi khách hàng thật gặp phải.

---

## 2. Bối cảnh nghiệp vụ

- **Khách hàng**: chủ xe của hãng, gọi tổng đài CSKH.
- **CSBot**: bot trả lời tự động qua điện thoại (ASR → LLM → TTS), do hãng vận hành.
- **Phạm vi đánh giá**: luồng *“khẩn cấp / cứu hộ”*. CSBot phải:
  1. Phân loại nhanh: cuộc gọi có phải tình huống khẩn cấp không.
  2. Khai thác đủ thông tin: vị trí, loại xe, mức độ nghiêm trọng, an toàn của
     người trên xe.
  3. Quyết định: điều xe cứu hộ / chuyển agent thật / hướng dẫn xử lý không
     khẩn cấp.
  4. Tuân thủ: không hứa hẹn vượt thẩm quyền, không thu thập PII vượt nhu cầu,
     không tư vấn pháp lý/y tế ngoài phạm vi, ngôn ngữ phù hợp.
- **Rủi ro chính cần phát hiện sớm**:
  - Bỏ sót tình huống khẩn cấp (false negative — đặc biệt nguy hiểm).
  - Quyết định cứu hộ sai (gọi cứu hộ khi không cần / không gọi khi cần).
  - Vi phạm compliance.
  - Lỗi ASR khi khách hoảng loạn, nói nhanh, giọng vùng miền.
  - **Lỗi audio đầu ra dù transcript đúng** — CSBot “nghĩ” mình đã nói đúng, nhưng
    audio khách nhận được bị méo / cắt / sai nội dung.

---

## 3. Hai vai trò trong hệ thống đánh giá

### 3.1 Human Bot — bot mô phỏng khách hàng

- Là **chính dự án này** (xem `README.md`): FreeSWITCH + `mod_audio_stream` + bridge
  Python + Gemini Live làm phần “trí tuệ”.
- Mỗi cuộc gọi được khởi tạo với một **kịch bản (scenario)** quy định:
  - `persona`: *“tài xế nam 35 tuổi, giọng miền Bắc, hơi hoảng”*…
  - `scenario`: *“xe va chạm nhẹ trên QL1 km 230, không có người bị thương, xe
    không nổ máy được”*…
  - `required_facts`: các thông tin **bắt buộc** Human Bot phải nói ra trong cuộc
    gọi (vị trí, biển số, mô tả sự cố, an toàn).
  - `forbidden_leaks`: các điều **tuyệt đối không được nói** (ví dụ: “tôi là bot”,
    “đây là bài kiểm tra”).
  - `expected_outcome`: ground truth dùng để chấm CSBot — ví dụ
    `{ emergency: true, towing: true, escalate_human: false }`.
- Trong cuộc gọi, Human Bot phải **đóng vai người thật**: nói tự nhiên, giữ
  trạng thái cảm xúc của persona, không tiết lộ là bot, **bám sát** kịch bản.

### 3.2 CSBot — bot cần đánh giá

- Là bot CSKH thật của hãng. AutoEval coi nó là **hộp đen**.
- Tương tác duy nhất qua đường thoại (SIP/PSTN). FreeSWITCH thu âm hai chiều.
- Nếu hãng cung cấp **log nội bộ** của CSBot (transcript ASR + text TTS), AutoEval
  dùng để đối chiếu chính xác hơn (xem tiêu chí *d* và *e*). Nếu không có, AutoEval
  vẫn chạy được nhưng kém chính xác hơn ở hai tiêu chí này.

---

## 4. Năm tiêu chí đánh giá

Mỗi cuộc gọi được chấm theo 5 tiêu chí dưới đây. Mỗi tiêu chí ghi rõ:
**đầu vào**, **phương pháp đo**, **chỉ số / ngưỡng pass-fail**, **đội chịu trách nhiệm**.

### a. Kịch bản của Human Bot có chuẩn không

> *Trước khi chạy*: bộ kịch bản dùng để test CSBot có đủ tốt để đo được năng
> lực thực sự của CSBot không?

- **Đầu vào**: file `scenarios/<id>.yaml` (persona, scenario, required_facts,
  forbidden_leaks, expected_outcome).
- **Phương pháp đo**:
  1. **Domain expert review**: chuyên gia CSKH / cứu hộ duyệt từng kịch bản về
     tính hiện thực, độ phủ (khẩn cấp vs không, tình huống mơ hồ, khách hoảng
     loạn, nhiều giọng vùng miền…).
  2. **LLM rubric review**: LLM-judge tự động chấm theo checklist (rõ ràng,
     không mâu thuẫn, có ground truth, có ít nhất một cặp emergency yes/no…).
  3. **Phân bố tổng**: đo % kịch bản theo nhóm (khẩn cấp vs không, ngày/đêm,
     vùng miền, độ phức tạp).
- **Chỉ số / ngưỡng**:
  - 100% kịch bản qua review (cả expert và LLM).
  - Phân bố không lệch quá ngưỡng — ví dụ ≥ 30% là emergency thật,
    ≤ 10% là kịch bản nhiễu/không hợp lệ, có ít nhất 5% biên (ambiguous).
- **Trách nhiệm**: đội thiết kế kịch bản (CSKH + QA).

### b. Human Bot có follow đúng kịch bản không

> *Trong cuộc gọi*: Human Bot có drift (đi chệch kịch bản) không? Nếu có,
> kết quả chấm CSBot ở các tiêu chí sau sẽ không còn giá trị.

- **Đầu vào**:
  - Kịch bản gốc.
  - `transcript_human.json` — transcript phía Human Bot, lấy từ Gemini Live
    `input_audio_transcription` + `output_audio_transcription` (đã sẵn trong
    bridge, xem `bridge/main.py`).
- **Phương pháp đo**:
  1. **Required-fact coverage**: với mỗi `required_facts`, kiểm tra Human Bot
     có nói ra trong cuộc gọi (regex cho slot có format cố định + LLM-judge
     ngữ nghĩa cho slot tự do).
  2. **Forbidden-leak detection**: Human Bot **không** được nói “tôi là bot”,
     “đây là bài test”, không tự bịa thông tin ngoài kịch bản.
  3. **Persona consistency**: LLM-judge so sánh giọng điệu/cảm xúc với persona
     (ví dụ “hoảng loạn nhẹ” ≠ “bình tĩnh trình bày”).
- **Chỉ số / ngưỡng**:
  - `fact_recall ≥ 0.90`.
  - `forbidden_leaks = 0`.
  - `persona_score ≥ 4 / 5`.
- **Trách nhiệm**: đội Human Bot (prompt engineering, model config).

### c. CSBot có trả lời đúng logic và không vi phạm compliance

> Đây là tiêu chí **chính** — đo trực tiếp năng lực CSBot.

- **Đầu vào**:
  - Full transcript hai chiều của cuộc gọi.
  - Kịch bản (để biết ground truth: `expected_outcome`).
  - Bộ rule compliance của hãng.
- **Phương pháp đo**:
  1. **Logic correctness** (LLM-judge có rubric):
     - Có hỏi đủ thông tin tối thiểu (vị trí, an toàn, loại sự cố)?
     - Phân loại emergency vs non-emergency có khớp ground truth không?
     - Quyết định cuối (gọi cứu hộ / chuyển agent / hướng dẫn tự xử lý) có
       khớp `expected_outcome` không?
     - Thứ tự câu hỏi có hợp lý không (an toàn người trước, thủ tục sau)?
  2. **Compliance check** (rule + LLM-judge):
     - Không hứa thời gian cứu hộ ngoài SLA cho phép.
     - Không yêu cầu PII không cần thiết (CMND, số tài khoản…).
     - Không tư vấn pháp lý/y tế ngoài thẩm quyền.
     - Ngôn ngữ phù hợp, xưng hô đúng vùng miền.
     - Có thông báo cuộc gọi được ghi âm (nếu policy yêu cầu).
- **Chỉ số / ngưỡng**:
  - `logic_score ≥ 4 / 5`.
  - `compliance_violations`: critical = 0, major ≤ 1.
  - **Confusion matrix trên trục emergency yes/no** — đo `recall` trên class
    `emergency = true` là **chỉ số quan trọng nhất**: false negative ở đây
    nghĩa là **bỏ sót khách đang gặp nạn**. Mục tiêu `recall ≥ 0.98`.
- **Trách nhiệm**: đội CSBot.

### d. Transcript (ASR) của CSBot có chính xác không

> Tách lỗi ASR ra khỏi lỗi logic. Nếu CSBot trả lời sai vì *nghe sai* từ đầu,
> đó là lỗi ASR, không phải lỗi LLM/policy — phải ghi rõ.

- **Đầu vào**:
  - `audio_human_bot.wav` — audio Human Bot đã phát (do FreeSWITCH ghi).
  - `text_human_bot.txt` — text Human Bot **dự định** nói (lấy từ Gemini Live
    `output_audio_transcription` của Human Bot — đây là ground truth gần nhất
    vì Gemini Live đã transcribe chính output của nó).
  - `transcript_csbot.json` — transcript do ASR của CSBot tạo ra (nếu hãng
    cấp log nội bộ).
- **Phương pháp đo**:
  1. **WER / CER** giữa `text_human_bot` và `transcript_csbot` từng turn.
  2. **Semantic match** (LLM-judge): “ASR của CSBot có hiểu **đúng ý** không?” —
     đề phòng WER cao nhưng ý đúng (đồng nghĩa) hoặc WER thấp nhưng đảo nghĩa.
  3. **Slot-level accuracy**: với các slot quan trọng (số điện thoại, biển số,
     vị trí), đo độ chính xác ở mức field thay vì toàn câu.
- **Chỉ số / ngưỡng**:
  - `wer ≤ 0.15` trên tập tổng.
  - `critical_slot_accuracy ≥ 0.95` (biển số, số km, số điện thoại).
  - `semantic_match ≥ 0.90`.
- **Trách nhiệm**: đội ASR của CSBot (hoặc đội tích hợp).
- **Khi không có log ASR của hãng**: dùng phản hồi của CSBot để **suy ngược**.
  Ví dụ Human Bot nói “km 230”, CSBot xác nhận lại “anh đang ở km 320 đúng
  không?” → đánh dấu nghi ngờ ASR sai. Phương pháp này kém chính xác và phải
  ghi rõ là *suy ngược*, không phải đo trực tiếp.

### e. Audio đầu ra của CSBot có chuẩn không

> Quan sát thực tế: **đôi khi transcript của CSBot đúng nhưng audio phát ra
> sai/lỗi**. Lỗi này nguy hiểm vì các tiêu chí (a)–(d) đều chấm trên text và
> sẽ pass, nhưng khách hàng thật **không nghe được nội dung đúng**.

- **Đầu vào**:
  - `audio_csbot_outbound.wav` — bản ghi audio CSBot phát ra (do FreeSWITCH ghi
    từ leg phía CSBot → Human Bot).
  - `text_csbot.txt` — text CSBot **nghĩ** mình đã nói (lấy từ log nội bộ của
    hãng; nếu không có thì lấy từ `transcript_csbot` của hãng — ít chính xác
    hơn).
- **Phương pháp đo**:
  1. **Re-ASR đối chiếu**: chạy một ASR độc lập (ví dụ Whisper large-v3 hoặc
     `gpt-4o-transcribe`) trên `audio_csbot_outbound.wav` → so sánh với
     `text_csbot`. Mismatch lớn ⇒ pipeline TTS / playout có vấn đề mặc dù
     CSBot “nghĩ” mình đã nói đúng.
  2. **Audio QA tự động**:
     - Phát hiện **silence / cut-off** (khoảng lặng đột ngột giữa câu).
     - Phát hiện **clipping** (méo do quá biên độ).
     - Phát hiện **lặp / nuốt từ** — so sánh độ dài audio với độ dài
       text dự kiến (lệch quá ±25% là bất thường).
     - **MOS estimate** bằng NISQA / UTMOS (ước lượng chất lượng cảm nhận).
  3. **Spot-check tai người**: mỗi tuần lấy ngẫu nhiên N% cuộc gọi cho QA
     nghe và đánh giá chủ quan; đối chiếu với chỉ số tự động để hiệu chỉnh
     ngưỡng.
- **Chỉ số / ngưỡng**:
  - `re_asr_wer ≤ 0.10` so với `text_csbot`.
  - Tỷ lệ cuộc gọi có flag audio (silence/clip/length-mismatch) `≤ 2%`.
  - `MOS_estimate ≥ 3.8` trung bình.
- **Trách nhiệm**: đội TTS / hạ tầng âm thanh CSBot.
- **Khi flag bật, lưu cả ba để chẩn đoán**:
  - `text_csbot` — phân biệt **lỗi TTS** (text đúng → audio sai)
    với **lỗi log** (text trong log ≠ text thật sự đưa vào TTS).
  - `audio_csbot_outbound.wav` — phân biệt **lỗi TTS engine**
    với **lỗi codec / RTP / mạng** (audio rời CSBot ổn nhưng đến Human Bot
    bị méo).
  - `transcript_reasr.json` — bằng chứng objective của những gì khách thật
    sẽ nghe.

---

## 5. Quy trình đánh giá tổng thể

```
┌────────────────────┐
│  Bộ kịch bản       │  ← review (a)
│  scenarios/*.yaml  │
└─────────┬──────────┘
          │
          ▼
┌────────────────────┐    SIP / PSTN     ┌───────────────────┐
│  Human Bot         │ ────────────────▶ │  CSBot (hãng)     │
│  FreeSWITCH +      │ ◀──────────────── │  ASR / LLM / TTS  │
│  bridge + Gemini   │                   └───────────────────┘
└─────────┬──────────┘
          │  ghi lại sau mỗi cuộc:
          │   • audio_human_bot.wav
          │   • audio_csbot_outbound.wav
          │   • transcript_human.json (Gemini Live)
          │   • transcript_csbot.json (log của hãng, nếu có)
          │   • metadata.json (uuid, scenario_id, timing)
          ▼
┌──────────────────────────────────────────────────────────┐
│  Tầng phân tích / chấm điểm (offline job)                │
│   • Re-ASR audio CSBot bằng Whisper       → tiêu chí (e) │
│   • Đối chiếu với transcript của hãng     → tiêu chí (d) │
│   • LLM-judge logic + compliance          → tiêu chí (c) │
│   • LLM-judge fact-coverage Human Bot     → tiêu chí (b) │
│   • Audio QA (silence / clip / MOS)       → tiêu chí (e) │
└─────────┬────────────────────────────────────────────────┘
          ▼
┌────────────────────┐
│  scores.json /     │  → tổng hợp daily / weekly dashboard
│  call report       │
└────────────────────┘
```

---

## 6. Dữ liệu cần thu thập trên mỗi cuộc gọi

| File / thư mục                          | Nguồn                                   | Phục vụ tiêu chí |
| --------------------------------------- | --------------------------------------- | ---------------- |
| `scenarios/<id>.yaml`                   | Đội kịch bản                            | a, b, c          |
| `calls/<uuid>/audio_human_bot.wav`      | FreeSWITCH `record_session` (in-leg)    | b, d, e          |
| `calls/<uuid>/audio_csbot.wav`          | FreeSWITCH `record_session` (out-leg)   | e                |
| `calls/<uuid>/audio_mix.wav`            | FreeSWITCH stereo recording             | spot-check tai   |
| `calls/<uuid>/transcript_human.json`    | Gemini Live (bridge, đã có sẵn)         | b, d             |
| `calls/<uuid>/transcript_csbot.json`    | Log nội bộ của hãng (nếu có)            | c, d             |
| `calls/<uuid>/transcript_reasr.json`    | Whisper trên `audio_csbot.wav`          | e                |
| `calls/<uuid>/metadata.json`            | Bridge + dialplan                       | mọi tiêu chí     |
| `calls/<uuid>/scores.json`              | Tầng chấm                               | báo cáo          |

> **Lưu ý**: dự án ở repo này **chưa thu thập** các artifact `audio_*.wav` và
> `transcript_*.json`. Đây là phần cần bổ sung — xem mục 7.

---

## 7. Lộ trình triển khai

### Giai đoạn 0 — đã xong
- FreeSWITCH + `mod_audio_stream` + bridge Python.
- Human Bot chạy được qua softphone, nói chuyện được với Gemini Live.
- Tài liệu setup trong `README.md`.

### Giai đoạn 1 — Human Bot có kịch bản
- Mở rộng bridge: nhận `scenario_id` qua query string của WebSocket → load
  `scenarios/<id>.yaml` → đẩy vào `system_instruction` của Gemini Live.
- Lưu `transcript_human.json` từ `input_audio_transcription` +
  `output_audio_transcription`.
- Bật `record_session` trong dialplan để có `audio_human_bot.wav`.

### Giai đoạn 2 — Gọi ra CSBot thật
- Cấu hình SIP gateway tới số PSTN/SIP của CSBot (mục Halonet trong bài gốc
  là một mẫu).
- Đảo chiều dialplan: thay vì softphone gọi vào, FreeSWITCH **originate**
  cuộc gọi tới CSBot rồi bắc cầu sang Human Bot.
- Ghi cả hai leg (`record_session both`) → có đủ
  `audio_human_bot.wav` + `audio_csbot.wav`.

### Giai đoạn 3 — Tầng chấm
- Job offline (Cloud Run / Cron) chạy sau mỗi cuộc gọi:
  - Re-ASR bằng Whisper.
  - LLM-judge cho (b) và (c).
  - Audio QA (silence detection, NISQA).
  - Ghi `scores.json`.
- Dashboard tổng hợp (Looker / Grafana) các chỉ số ở mục 4.

### Giai đoạn 4 — Bộ kịch bản & quy trình review
- Thư viện `scenarios/` với schema cố định.
- Quy trình PR review (domain expert + LLM-judge tự động) **trước khi**
  merge kịch bản mới.
- Versioning: mỗi cuộc gọi log lại `scenario_version` để tránh trộn kết quả
  khi đổi kịch bản giữa kỳ.

---

## 8. Rủi ro và lưu ý

- **Human Bot drift**: Gemini Live có thể “phá kịch bản” khi CSBot hỏi ngoài
  luồng. Cần rubric (b) chặt và cảnh báo sớm; nếu `fact_recall` thường xuyên
  < 90%, cân nhắc tách prompt thành state machine cứng hơn (function calling
  thay cho free-form).
- **Phụ thuộc log của hãng**: tiêu chí (d) và (e) chính xác hơn nhiều khi có
  log ASR + log text TTS của CSBot. Nếu hãng không cấp, ghi rõ chỉ số nào
  là *đo trực tiếp*, chỉ số nào là *suy ngược*.
- **Chi phí**: mỗi cuộc gọi tốn quota Gemini Live (Human Bot) + Whisper
  (re-ASR) + LLM-judge × 2-3. Cần ước tính và đặt budget alert trước khi
  scale lên.
- **Privacy**: dù dùng persona giả, audio cuộc gọi với CSBot vẫn nên được
  mã hoá at-rest và có policy retention rõ ràng. Tránh để lẫn dữ liệu PII
  thật của developer khi test.
- **A/B regression**: mỗi khi hãng cập nhật CSBot, chạy lại **cùng một bộ
  kịch bản cố định** trên phiên bản cũ và mới rồi so sánh. Nên chốt một
  *regression set* không thay đổi giữa các phiên bản CSBot.
- **Audio bug khó tái hiện** (tiêu chí e): vì chỉ xuất hiện ngẫu nhiên, phải
  giữ đủ raw audio + log để replay khi nó xảy ra. **Đừng** xoá audio sớm
  ngay cả khi cuộc gọi pass — bug này thường chỉ thấy khi nghe lại.

---

## 9. Tham khảo

- `README.md` — kiến trúc và cách dựng Human Bot runtime.
- Bài gốc: [Gemini Live Part 1 — FreeSWITCH + ADK](https://discuss.google.dev/t/gemini-live-part-1-building-a-low-latency-telephone-voice-agent-with-freeswitch-and-adk-agents-powered-by-gemini-live/332641)
- Whisper (re-ASR): <https://github.com/openai/whisper>
- NISQA / UTMOS (ước lượng MOS): <https://github.com/gabrielmittag/NISQA>
