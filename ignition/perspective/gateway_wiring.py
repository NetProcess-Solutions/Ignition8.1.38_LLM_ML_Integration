"""
Sprint 7 / B13 + A5 + A6 — Ignition Perspective gateway-side wiring.

PLACEHOLDER MODULE. The Perspective gateway runs Jython 2.7, NOT
Python 3, so this file is a SPECIFICATION + TEMPLATES only. Copy
the function bodies into the corresponding gateway scripts named
in each docstring.

The three pieces that need to live on the gateway:

  B13: `chat.send`             — wraps the Perspective Chat View's
                                  `onSendMessage` event. Captures the
                                  curated context from the operator's
                                  current line, posts to /api/chat,
                                  renders the response in the view.

  A5:  `chat.feedback`         — fires when the operator clicks
                                  thumbs-up/down or marks an outcome
                                  as confirmed/rejected. Posts to
                                  /api/feedback or /api/outcomes.

  A6:  `tag_change.uns_alarm`  — fires on the UNS alarm-active edge.
                                  Auto-launches a "diagnose this
                                  alarm" chat thread so the operator
                                  doesn't have to re-state context.

Each function below shows the EXACT shape the gateway script should
take. Comments mark the project-specific bits the user must fill in
(camera tags, line_id detection, view path, API key vault entry).
"""
from __future__ import annotations


# =====================================================================
# B13 — chat.send  (gateway script: project/scripts/chat/send.py)
# =====================================================================
SEND_TEMPLATE = '''
# Jython 2.7 — runs in Ignition Perspective gateway.
# Place at: project/scripts/chat/send.py
# Bind to: Chat View's onSendMessage event.
import system

API_BASE = "http://chatbot-service:8080"
API_KEY  = system.tag.readBlocking(["[Memory]chatbot/api_key"])[0].value

def on_send(self, event):
    # 1. Resolve which line the operator is viewing.
    #    TODO: replace with however your project tags the active line
    #    on a session prop. Default below assumes a session custom prop.
    line_id = self.session.custom.activeLineId or "coater1"

    # 2. Grab the curated context from the gateway-side context module.
    #    Wires through ignition/scripts/context.py (already implemented
    #    in this repo). That module returns a dict matching
    #    CuratedContextPackage.
    from chatbot import context as ctx_mod
    curated = ctx_mod.build(line_id=line_id, query=event.message)

    # 3. POST /api/chat.
    body = {
        "query":        event.message,
        "session_id":   str(self.session.id),
        "user_id":      str(self.session.props.auth.user.userName),
        "line_id":      line_id,
        "live_context": curated,
        "conversation_id": self.view.params.conversationId,  # may be None
    }
    resp = system.net.httpClient().post(
        API_BASE + "/api/chat",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        data=system.util.jsonEncode(body),
    )

    # 4. Render the response in the chat scrollback.
    payload = system.util.jsonDecode(resp.text)
    self.view.params.conversationId = payload["conversation_id"]
    self.view.custom.lastMessageId  = payload["message_id"]
    self.props.messages.append({
        "role":       "assistant",
        "content":    payload["response"],
        "sources":    payload["sources"],
        "confidence": payload["confidence"],
        "messageId":  payload["message_id"],
    })
'''


# =====================================================================
# A5 — chat.feedback (gateway script: project/scripts/chat/feedback.py)
# =====================================================================
FEEDBACK_TEMPLATE = '''
# Jython 2.7. Place at: project/scripts/chat/feedback.py
# Bind to: thumbs-up, thumbs-down, "confirm root cause", "reject root cause"
# buttons in the chat view.
import system

API_BASE = "http://chatbot-service:8080"
API_KEY  = system.tag.readBlocking(["[Memory]chatbot/api_key"])[0].value


def submit(self, message_id, signal_type, value, note=""):
    """
    signal_type: one of FeedbackSignalType in models/schemas.py.
        usefulness, correctness, root_cause_confirmed,
        root_cause_rejected, recommendation_acted_on, ...
    value: 1 / -1 / 0 (or a 0..1 score; service accepts both)
    """
    body = {
        "message_id":   message_id,
        "signal_type":  signal_type,
        "value":        value,
        "user_id":      str(self.session.props.auth.user.userName),
        "note":         note,
    }
    system.net.httpClient().post(
        API_BASE + "/api/feedback",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        data=system.util.jsonEncode(body),
    )


def link_outcome(self, message_id, outcome_table, outcome_id, alignment, note=""):
    """
    Called when the operator marks the recommendation as confirmed or
    rejected by linking it to a real defect/quality/downtime row.
    alignment: "confirmed" | "rejected" | "partial".
    """
    body = {
        "message_id":     message_id,
        "outcome_type":   "manual_link",
        "outcome_id":     outcome_id,
        "outcome_table":  outcome_table,    # quality_results / defect_events / downtime_events
        "alignment":      alignment,
        "linked_by":      str(self.session.props.auth.user.userName),
        "notes":          note,
    }
    system.net.httpClient().post(
        API_BASE + "/api/outcomes",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        data=system.util.jsonEncode(body),
    )
'''


# =====================================================================
# A6 — tag_change.uns_alarm
# (gateway script: project/scripts/chatbot/auto_diagnose.py +
#  a tag-change script on each UNS alarm tag)
# =====================================================================
TAG_CHANGE_TEMPLATE = '''
# Jython 2.7. Place at: project/scripts/chatbot/auto_diagnose.py
# Bind by adding a Tag Change script to each UNS alarm tag of interest:
#
#     project.chatbot.auto_diagnose.on_alarm_active(tag, currentValue)
#
# (Or, more scalable, attach a Gateway Tag Change Script that filters
# on UDT alarm sub-tag pattern.)
import system

API_BASE = "http://chatbot-service:8080"
API_KEY  = system.tag.readBlocking(["[Memory]chatbot/api_key"])[0].value


def on_alarm_active(tag, currentValue):
    """Auto-launches a chat thread on each rising-edge alarm activation."""
    if currentValue.value not in (True, 1):
        return  # ignore returns to normal

    # TODO: derive line_id and human-readable alarm label from the tag path.
    line_id = tag.tagPath.split("/")[1]      # e.g. ".../coater1/alarms/x"
    label   = tag.tagPath.split("/")[-1]

    # Build the autostart payload — a synthesized "what just happened?" query.
    from chatbot import context as ctx_mod
    curated = ctx_mod.build(line_id=line_id, query="alarm: " + label)

    body = {
        "query":         "Alarm just activated: " + label + ". What's likely going on, and what should I check first?",
        "session_id":    "auto-diagnose-" + str(system.date.now().getTime()),
        "user_id":       "system",   # system-attributed; real operator can claim later
        "line_id":       line_id,
        "live_context":  curated,
        "conversation_id": None,
    }
    system.net.httpClient().post(
        API_BASE + "/api/chat",
        headers={"X-API-Key": API_KEY, "Content-Type": "application/json"},
        data=system.util.jsonEncode(body),
    )
    # The resulting message_id is persisted server-side; surface it in
    # the alarm-summary popup with a "View AI diagnosis" button.
'''


# =====================================================================
# Helper: print all templates so a maintainer can copy-paste them out.
# =====================================================================
if __name__ == "__main__":
    print("=" * 70)
    print("B13 — Chat send (gateway script)")
    print("=" * 70)
    print(SEND_TEMPLATE)
    print("=" * 70)
    print("A5 — Feedback / outcome linkage (gateway script)")
    print("=" * 70)
    print(FEEDBACK_TEMPLATE)
    print("=" * 70)
    print("A6 — Auto-diagnose on alarm (tag change script)")
    print("=" * 70)
    print(TAG_CHANGE_TEMPLATE)
