# ai/client.py
# HTTP client for the IgnitionChatbot FastAPI service.
# Jython 2.7 compatible.

import system
import ai.config as cfg

_log = system.util.getLogger(cfg.LOGGER_NAME + ".client")
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = system.net.httpClient(timeout=cfg.REQUEST_TIMEOUT_MS)
    return _client


def _post(path, payload):
    url = cfg.AI_SERVICE_URL.rstrip("/") + path
    headers = {
        "Content-Type": "application/json",
        "X-API-Key":    cfg.API_KEY,
    }
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


def postJson(path, payload):
    """Public wrapper around the internal _post helper."""
    return _post(path, payload)


def sendQuery(userMessage, sessionId, userId, lineId=None, conversationId=None):
    """
    Sends a chat query along with the curated live context to the AI service.
    Returns a dict like:
        { ok: True/False, data: {...response...}, error: '...' }
    """
    import ai.context as context
    line = lineId or cfg.LINE_ID
    # Pre-screen: ask the service which tags are likely relevant so we don't
    # read/historian-query all 80+ on every call.
    selected = None
    if getattr(cfg, "USE_TAG_SELECTOR", False):
        try:
            import ai.selector as selector
            selected = selector.selectRelevantTagNames(userMessage, line)
        except Exception as e:
            _log.warn("tag selector failed, sending core-only: %s" % str(e))
            selected = None
    live = context.buildCuratedContext(line, selectedTagNames=selected)
    payload = {
        "query":         userMessage,
        "session_id":    sessionId,
        "user_id":       userId,
        "line_id":       line,
        "live_context":  live,
    }
    if conversationId:
        payload["conversation_id"] = conversationId
    return _post("/api/chat", payload)


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
    return _post("/api/feedback", payload)


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
    return _post("/api/corrections", payload)


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
    return _post("/api/outcomes", payload)
