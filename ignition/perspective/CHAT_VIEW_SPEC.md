# Perspective Chat View — v2.0 Specification

This is the implementation spec for the Coater 1 Intelligent Operations
Advisor chat view. The actual view JSON has to be authored in Ignition
Designer because the Designer round-trips view metadata and UDT bindings;
this document describes every required component, binding, and event
script so the Designer build is mechanical.

Reference: design document section 6 (Ignition Perspective UX).

---

## 1. View root

- **Type**: `coords` container, breakpoint `coater1Chat`.
- **Bindings**:
  - `props.session.userId` ← gateway property, `{[System]Client/User/Username}`.
  - `props.session.lineId` ← `coater1` (constant for MVP; promote to
    selector when other lines onboard).
  - `props.session.sessionId` ← view-load script:
    `system.util.getProperty("sessionId") or system.util.uuid()`.
  - `props.session.conversationId` ← null until the first response comes back.

---

## 2. Top bar (`coords.topBar`)

| Component | Type | Notes |
|-----------|------|-------|
| `lblTitle` | Label | "Coater 1 Operations Advisor" |
| `lblConfidence` | Label | Bound to `view.custom.lastConfidence`. Color rules: high=`#1B7B45`, medium=`#B7791F`, low=`#A14A24`, insufficient_evidence=`#7A1E1E`. |
| `btnNewConversation` | Button | onClick: `view.custom.conversationId = None; view.custom.messages = []` |

---

## 3. Message list (`coords.messageList`)

- **Type**: `flex` column, scroll vertical.
- **Repeat** over `view.custom.messages` (list of `{role, content, sources, confidence, anchor, excludedBuckets}`).
- Each message bubble:
  - `MessageBubble.user` style for `role == "user"`.
  - `MessageBubble.assistant` style for `role == "assistant"`.
  - Citation pills rendered inline by walking `sources[*].id` references in `content`.
  - Footer label showing `confidence` with the same color map as the top bar.

### 3.1 Anchor banner (assistant bubbles)

When `message.anchor` is present, render a small banner at the TOP of the
bubble:

> **Interpreted as:** past-event query at 2024-06-14 10:30 UTC for run R-20240614-02 (style S-1234, mode delam_hotpull).
>
> *If this is wrong, click here to reframe.* — onClick opens the
> clarification modal (section 5).

### 3.2 Excluded-bucket badges

If `message.excludedBuckets` is non-empty, render them as muted gray pills:

> `live_tags excluded — past_event` &nbsp; `live_alarms excluded — past_event`

This makes the structural exclusions visible to the operator and is the
mechanism that prevents the operator from thinking "the bot ignored
current state by mistake".

---

## 4. Source panel (`coords.sourcePanel`)

- **Type**: collapsible side drawer, toggled by `btnToggleSources` in the top bar.
- **Repeat** over `view.custom.lastSources`.
- Each row:
  - **Provenance badge** with the v2 taxonomy color:
    - `LIVE_TAG` blue, `HISTORIAN_STAT` blue-grey, `DEVIATION` orange,
    - `BASELINE_COMPARE` purple, `MATCHED_HISTORY` green,
    - `ALARM` red, `EVENT` red-orange, `WORK_ORDER` brown,
    - `DOCUMENT` slate, `CAMERA_CLIP` teal, `RULE` indigo,
    - `MEMORY` gold, `ML_PREDICTION` magenta.
  - **Citation id** (e.g. `[14]`).
  - **Title** + truncated excerpt.
  - **Score** if present.
  - **Action buttons**:
    - For `DOCUMENT`/`MEMORY`/`WORK_ORDER`: "Open"
    - For `CAMERA_CLIP`: "Play" — calls Symphony Player URL from
      `metadata.storage_handle`.
    - For any source: "Mark wrong" — sends `sendCorrection` (see section 6).

---

## 5. Clarification modal (`coords.clarificationModal`)

- Triggered by anchor banner click OR when `response.anchor.anchor_status`
  starts with `needs_clarification_`.
- Modes:
  - `needs_clarification_enumerated`: render `anchor.clarification_options`
    as buttons. onClick: re-send query with `live_context.anchor` set to
    the chosen option's `value`.
  - `needs_clarification_open`: render the `anchor.clarification_prompt`
    plus a free-text input that re-sends the original query prefixed with
    the user's clarification.
  - `needs_clarification_scoped`: same as enumerated but with a
    "broaden the search" button that re-sends with a wider time window.

---

## 6. Tag-bucket evidence widgets (`coords.tagEvidencePanel`)

- Renders the new v2 tag_evidence section of CuratedContextPackage when
  the message has it.
- For each `TagBucketEvidence`:
  - Header: tag name + class.
  - Rows: one per `BaselineWindow` showing mean / min / max / std plus
    a small ASCII or SVG box-plot widget. The orchestrator already
    embeds an ASCII box plot in the prompt; the same data is in the
    citation `metadata` so we can render an SVG version in Perspective.
  - Color rule: if `current` falls outside `[min, max]`, the row is red.

---

## 7. Footer (`coords.footer`)

- `txtInput` — chat input.
- `btnSend` — onClick: call `system.util.invokeAsynchronous(_sendQuery)`.
- `btnAttachClip` — opens a list of recent Symphony events (last 24h)
  pulled by `ai.client.fetchRecentClips()` (TODO — Phase 4 — for now,
  manual `event_id` entry).
- `btnRootCauseConfirmed` — visible only after the assistant turn shows
  `confidence in (high, medium)` AND mentions a defect event in its
  citations. onClick: `ai.client.confirmRootCause(messageId, userId,
  defectEventId, True)`.

---

## 8. View scripts

```python
# Custom method: _sendQuery
def _sendQuery(self):
    msg = self.getChild("footer").getChild("txtInput").props.text
    if not msg.strip():
        return
    self.custom.messages.append({"role": "user", "content": msg})
    import ai.client as client
    resp = client.sendQuery(
        userMessage=msg,
        sessionId=self.session.props.sessionId,
        userId=self.session.props.userId,
        lineId=self.session.props.lineId,
        conversationId=self.custom.conversationId,
    )
    if resp["ok"]:
        d = resp["data"]
        self.custom.conversationId = d["conversation_id"]
        self.custom.lastConfidence = d["confidence"]
        self.custom.lastSources = d["sources"]
        self.custom.messages.append({
            "role": "assistant",
            "content": d["response"],
            "sources": d["sources"],
            "confidence": d["confidence"],
            "anchor": d.get("context_summary", {}).get("parsed_anchor"),
            "excludedBuckets": d.get("context_summary", {}).get("excluded_buckets", []),
            "messageId": d["message_id"],
        })
    else:
        self.custom.messages.append({
            "role": "assistant",
            "content": "[Service error: %s]" % resp.get("error"),
            "confidence": "service_error",
        })
    self.getChild("footer").getChild("txtInput").props.text = ""
```

---

## 9. Designer build checklist

1. Create new view `Coater1Chat` under `Perspective/Views/AI`.
2. Add the components in the order above; bind to `view.custom.*` and
   `view.session.*` as listed.
3. Import the four custom methods from section 8 into `view.custom`.
4. Set initial `view.custom = { messages: [], conversationId: None,
   lastConfidence: None, lastSources: [] }`.
5. Style sheet: import `themes/coater1Chat.css` (color map from section 3
   and section 4).
6. Embed the view into the `Coater1/Operator` page in the right-hand pane.
7. Test using the seven query types from design section 8 task 10.

The view JSON export lives at `ignition/perspective/Coater1Chat.proj`
once Designer publishes it.
