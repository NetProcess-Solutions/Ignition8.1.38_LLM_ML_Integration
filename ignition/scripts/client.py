# ai/client.py
# HTTP client for the IgnitionChatbot FastAPI service.
# Jython 2.7 compatible.

import system
import time
import ai.config as cfg

_log = system.util.getLogger(cfg.LOGGER_NAME + ".client")
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = system.net.httpClient(timeout=cfg.REQUEST_TIMEOUT_MS)
    return _client


# -----------------------------------------------------------------------------
# Per-user signed token (Sprint 1 / A4) - HMAC-SHA256 JWT.
# -----------------------------------------------------------------------------
def _b64url(byte_array):
    """Base64-url encode raw bytes, no padding (Jython-safe)."""
    from java.util import Base64
    return Base64.getUrlEncoder().withoutPadding().encodeToString(byte_array)


def _hmac_sha256(secret_str, message_str):
    from javax.crypto import Mac
    from javax.crypto.spec import SecretKeySpec
    key = SecretKeySpec(secret_str.encode("utf-8"), "HmacSHA256")
    mac = Mac.getInstance("HmacSHA256")
    mac.init(key)
    return mac.doFinal(message_str.encode("utf-8"))


def signedToken(userId, sessionId=None):
    """Build a short-lived HS256 JWT identifying (userId, sessionId, gateway)."""
    now = int(time.time())
    header  = {"alg": "HS256", "typ": "JWT"}
    payload = {
        "user_id":    userId,
        "gateway_id": cfg.GATEWAY_ID,
        "iat":        now,
        "exp":        now + int(cfg.GATEWAY_TOKEN_TTL_S),
    }
    if sessionId:
        payload["session_id"] = sessionId
    h = _b64url(system.util.jsonEncode(header).encode("utf-8"))
    p = _b64url(system.util.jsonEncode(payload).encode("utf-8"))
    signing_input = h + "." + p
    sig = _b64url(_hmac_sha256(cfg.GATEWAY_HMAC_SECRET, signing_input))
    return signing_input + "." + sig


def _post(path, payload, userId=None, sessionId=None):
    url = cfg.AI_SERVICE_URL.rstrip("/") + path
    headers = {
        "Content-Type": "application/json",
        "X-API-Key":    cfg.API_KEY,
    }
    if userId:
        try:
            headers["Authorization"] = "Bearer " + signedToken(userId, sessionId)
        except Exception as e:
            _log.warn("Failed to mint user token: %s" % str(e))
    try:
        resp = _get_client().post(url, data=payload, headers=headers)
    except Exception as e:
        _log.error("HTTP POST failed for %s: %s" % (path, str(e)))
        return {
            "ok": False,
            "error": "transport: " + str(e),
            "status_code": None,
        }
    if not resp.good:
        body = ""
        try:
            body = resp.text or ""
        except Exception:
            pass
        _log.warn("Bad response %s from %s: %s" % (resp.statusCode, path, body[:500]))
        return {
            "ok": False,
            "status_code": resp.statusCode,
            "error": body or ("HTTP " + str(resp.statusCode)),
        }
    try:
        return {"ok": True, "status_code": resp.statusCode, "data": resp.json}
    except Exception as e:
        _log.error("Failed to parse JSON from %s: %s" % (path, str(e)))
        return {"ok": False, "status_code": resp.statusCode, "error": "bad json"}


def postJson(path, payload, userId=None, sessionId=None):
    """Public wrapper around the internal _post helper."""
    return _post(path, payload, userId=userId, sessionId=sessionId)


def sendQuery(userMessage, sessionId, userId, lineId=None, conversationId=None,
              anchorTime=None, attachedClips=None):
    """
    Sends a chat query along with the curated live context to the AI service.
    Optional kwargs (design v2.0):
      anchorTime    : ISO-8601 string. Attached as live_context.anchor.anchor_time;
                      the orchestrator's anchor parser uses it as the resolved time.
      attachedClips : list of dicts with keys event_id/clip_start/clip_end/
                      camera_id/storage_handle (matches CameraClipRef schema).
    Returns a dict like:
        { ok: True/False, data: {...response...}, error: '...' }
    """
    import ai.context as context
    line = lineId or cfg.LINE_ID
    selected = None
    if getattr(cfg, "USE_TAG_SELECTOR", False):
        try:
            import ai.selector as selector
            selected = selector.selectRelevantTagNames(userMessage, line)
        except Exception as e:
            _log.warn("tag selector failed, sending core-only: %s" % str(e))
            selected = None
    live = context.buildCuratedContext(line, selectedTagNames=selected)
    if attachedClips:
        live["attached_clips"] = attachedClips
    if anchorTime:
        live["anchor"] = {
            "anchor_type": "past_event",
            "anchor_time": anchorTime,
            "anchor_status": "resolved",
        }
    payload = {
        "query":         userMessage,
        "session_id":    sessionId,
        "user_id":       userId,
        "line_id":       line,
        "live_context":  live,
    }
    if conversationId:
        payload["conversation_id"] = conversationId
    return _post("/api/chat", payload, userId=userId, sessionId=sessionId)


def sendFeedback(messageId, userId, signalType, signalValue, comment=None):
    """
    signalType  : 'usefulness'|'correctness'|'completeness'|'source_relevance'|...
    signalValue : 'positive'|'negative'|'neutral'
    """
    payload = {
        "message_id":   messageId,
        "user_id":      userId,
        "signal_type":  signalType,
        "signal_value": signalValue,
    }
    if comment:
        payload["comment"] = comment
    return _post("/api/feedback", payload, userId=userId)


def sendCorrection(messageId, userId, correctionType, correctedClaim,
                   originalClaim=None, supportingEvidence=None):
    payload = {
        "message_id":      messageId,
        "user_id":         userId,
        "correction_type": correctionType,
        "corrected_claim": correctedClaim,
    }
    if originalClaim:
        payload["original_claim"] = originalClaim
    if supportingEvidence:
        payload["supporting_evidence"] = supportingEvidence
    return _post("/api/corrections", payload, userId=userId)


def linkOutcome(messageId, outcomeType, outcomeId, outcomeTable, alignment,
                linkedBy, notes=None):
    payload = {
        "message_id":    messageId,
        "outcome_type":  outcomeType,
        "outcome_id":    outcomeId,
        "outcome_table": outcomeTable,
        "alignment":     alignment,
        "linked_by":     linkedBy,
    }
    if notes:
        payload["notes"] = notes
    return _post("/api/outcomes", payload, userId=linkedBy)


def confirmRootCause(messageId, userId, defectEventId, confirmed, notes=None):
    """
    Operator clicked "Root cause confirmed?" on a chat answer. Records the
    confirmation as an outcome linkage so the same scenario in the future
    weighs MEMORY higher (design v2.0 section 4.4).
    """
    return linkOutcome(
        messageId=messageId,
        outcomeType="root_cause_confirmation",
        outcomeId=str(defectEventId),
        outcomeTable="defect_events",
        alignment="confirmed" if confirmed else "rejected",
        linkedBy=userId,
        notes=notes,
    )
