# Perspective Views — Build Guide

The MVP needs one Perspective view: **`ChatView`** (the operator/engineer chat
interface). Optional secondary views can be added later for memory curation
and audit.

We provide build instructions rather than raw view JSON because Perspective
view JSON is tightly coupled to your Ignition gateway version and project
configuration — exporting from a Designer that matches your environment is
more reliable than importing a hand-edited JSON blob.

---

## View 1 — `ChatView` (required for MVP)

### Layout

A **Coordinate** or **Flex (column)** root container with three regions:

| Region | Purpose |
|--------|---------|
| Header (top, ~50 px) | Title "Coater 1 AI Assistant", maybe user display name |
| Body (fills) | Two-column flex: chat (left, ~70%) + context dashboard (right, ~30%) |
| Footer (bottom, ~80 px) | Text input + Send button |

### Custom session properties (set via Project Browser > Session Properties)

```
session.custom.chatMessages    : array  (default: [])
session.custom.isProcessing    : boolean (default: false)
session.custom.conversationId  : string  (default: "")
session.custom.lastSourcesById : object  (default: {})
```

### Custom view properties (`view.params` / `view.custom`)

```
view.custom.lineId : string (default: "coater1")
```

### Chat panel (left column)

A **Flex (column)** container. Inside:

1. **Flex Repeater** (`flex-repeater`) bound to `session.custom.chatMessages`
   - Each item renders an embedded view `ChatMessage` (see below) with
     `view.params.message = value`.
   - Direction = column, scroll = enabled, `props.style.flex = "1 1 auto"`.

2. (Optional) A **Label** below the repeater shown only when
   `session.custom.isProcessing` is true: "Thinking..." with a small spinner icon.

### Context dashboard (right column)

A **Flex (column)** container with sections:

- **Coater 1 Status** label + `Table` or stack of `Label`s with tag
  bindings to your real Coater 1 tags (LineSpeed, ZoneTemp1/2/3,
  CoatingWeight, etc.). These bind directly to tags — fast and
  independent of the chat backend.
- **Active Alarms** — `Alarm Status Table` component filtered to
  `*Coater1*`.
- **Current Recipe** — Labels bound to recipe tags.

### Footer (input + send)

A **Flex (row)** with:

- **Text Area** (`text-area`) bound to `view.custom.userInput`. Set
  `props.style.flex = "1 1 auto"` and `props.rows = 2`.
- **Button** "Send" with the script below on `onActionPerformed`.

### Send button script (`onActionPerformed`)

```python
# Runs in Gateway scope inside the Perspective session.
import ai.client

userMessage = self.view.custom.userInput
if not userMessage or not userMessage.strip():
    return

session = self.session
sessionId  = session.props.id
userId     = session.props.auth.user.userName or "anonymous"
lineId     = self.view.custom.lineId or "coater1"
convoId    = session.custom.conversationId or None

# Append user message to chat immediately
msgs = list(session.custom.chatMessages or [])
msgs.append({
    "role":       "user",
    "content":    userMessage,
    "timestamp":  system.date.format(system.date.now(), "HH:mm:ss"),
})
session.custom.chatMessages = msgs
session.custom.isProcessing = True
self.view.custom.userInput = ""

# Call the AI service (synchronous; runs in gateway scope)
result = ai.client.sendQuery(
    userMessage = userMessage,
    sessionId   = sessionId,
    userId      = userId,
    lineId      = lineId,
    conversationId = convoId,
)

session.custom.isProcessing = False

if not result.get("ok"):
    msgs = list(session.custom.chatMessages)
    msgs.append({
        "role":      "assistant",
        "content":   "Service error: " + str(result.get("error", "unknown")),
        "confidence":"insufficient_evidence",
        "sources":   [],
        "messageId": None,
        "timestamp": system.date.format(system.date.now(), "HH:mm:ss"),
    })
    session.custom.chatMessages = msgs
    return

data = result["data"]
session.custom.conversationId = data.get("conversation_id")

# Cache sources by message_id for the source-detail panel
sources_map = dict(session.custom.lastSourcesById or {})
sources_map[data["message_id"]] = data.get("sources", [])
session.custom.lastSourcesById = sources_map

msgs = list(session.custom.chatMessages)
msgs.append({
    "role":       "assistant",
    "content":    data["response"],
    "confidence": data["confidence"],
    "sources":    data.get("sources", []),
    "messageId":  data["message_id"],
    "timestamp":  system.date.format(system.date.now(), "HH:mm:ss"),
})
session.custom.chatMessages = msgs
```

---

## View 2 — `ChatMessage` (embedded, rendered per message)

This view is rendered once per message by the Flex Repeater above.

### Parameters
- `view.params.message` (object) — passed in by the repeater

### Layout
A **Flex (column)** container, padding ~8 px, with conditional styling:

- If `view.params.message.role == "user"`: align right, light blue background.
- If `view.params.message.role == "assistant"`: align left, light gray
  background, border-left colored by confidence:
  - `confirmed` → green
  - `likely` → blue
  - `hypothesis` → orange
  - `insufficient_evidence` → red

### Components inside

1. **Label** for the role + timestamp:
   `{view.params.message.role | upper} - {view.params.message.timestamp}`

2. **Markdown** component for content:
   `props.markdown` bound to `view.params.message.content`
   (Markdown rendering preserves the `[1] [2]` citations as plain text.)

3. **Label** showing confidence:
   `"Confidence: " + (view.params.message.confidence or "n/a")`
   Only visible when role == "assistant".

4. **Flex (row)** with feedback buttons. Visible only when role == "assistant"
   AND `view.params.message.messageId != None`. Buttons:

   - **👍 Useful** — `onActionPerformed`:
     ```python
     import ai.client
     ai.client.sendFeedback(
         messageId   = self.view.params.message.messageId,
         userId      = self.session.props.auth.user.userName or "anonymous",
         signalType  = "usefulness",
         signalValue = "positive",
     )
     ```
   - **👎 Not useful** — same as above but `signalValue = "negative"`.
   - **Correct** — same with `signalType = "correctness"`,
     `signalValue = "positive"`.
   - **Incorrect** — opens a popup view to capture a correction (see below).

5. **Collapsible "Show sources"** — a button that toggles a Markdown component
   showing the sources list:
   ```
   {sources_as_markdown}
   ```
   You can build the markdown inline in a property change script:
   ```python
   sources = self.view.params.message.sources or []
   lines = []
   for s in sources:
       title = s.get("title") or "(untitled)"
       excerpt = s.get("excerpt") or ""
       lines.append("**[" + str(s["id"]) + "]** _(" + str(s.get("type")) + ")_ " + title)
       if excerpt:
           lines.append("&nbsp;&nbsp;&nbsp;" + excerpt)
   self.custom.sourcesMarkdown = "\n\n".join(lines)
   ```

---

## View 3 — `CorrectionPopup` (optional, recommended)

A small popup view for capturing structured corrections.

### Parameters
- `view.params.messageId`
- `view.params.originalClaim` (string, optional)

### Components
- **Dropdown** for `correction_type` with options matching the API enum:
  `factual_error`, `wrong_root_cause`, `missing_context`, `wrong_equipment`,
  `outdated_info`, `misleading_conclusion`, `other`
- **Text Area** for `corrected_claim` (required)
- **Text Area** for `supporting_evidence` (optional)
- **Button** "Submit":

```python
import ai.client
result = ai.client.sendCorrection(
    messageId        = self.view.params.messageId,
    userId           = self.session.props.auth.user.userName or "anonymous",
    correctionType   = self.getChild("Dropdown_Type").props.value,
    correctedClaim   = self.getChild("TextArea_Corrected").props.text,
    originalClaim    = self.view.params.originalClaim,
    supportingEvidence = self.getChild("TextArea_Evidence").props.text,
)
if result.get("ok"):
    system.perspective.closePopup("correctionPopup")
```

---

## Notes on Designer security

- Restrict the chat view to roles you want (Operators, Engineers, Quality,
  Maintenance) via the view's permission settings.
- The Memory Curation view (Phase 3) should require an Engineer role.
- The audit / metrics view (later) should require Admin or Supervisor.
