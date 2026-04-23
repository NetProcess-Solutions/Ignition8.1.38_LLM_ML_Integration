# IgnitionChatbot - Gateway Project Library Scripts (Jython 2.7)

These three scripts go in your Ignition project's **Script Library**
(Project Browser > Scripting > Script Library), under a top-level package
called `ai`.

```
ai/
  config.py    # constants and tag list
  client.py    # HTTP client wrapping system.net.httpClient
  context.py   # builds the CuratedContextPackage from live plant data
```

## Install

1. Open the Ignition Designer.
2. In Project Browser, expand **Scripting > Script Library**.
3. Right-click > **New Script Package** > name it `ai`.
4. Inside `ai`, create three scripts: `config`, `client`, `context`.
5. Copy the contents of the matching `.py` file from this folder into each
   script.
6. Save the project.

## How it's called

From a Perspective view's button event (`onActionPerformed`), or from a
session/page message handler, you call:

```python
result = ai.client.sendQuery(
    userMessage = self.view.params.userMessage,
    sessionId   = self.session.props.id,
    userId      = self.session.props.auth.user.userName or 'anonymous',
    lineId      = 'coater1',
)
# result is a dict with keys: response, sources, confidence,
# context_summary, message_id, conversation_id, processing_time_ms
```

## Configuration

Edit `ai/config.py` to set:

- `AI_SERVICE_URL` - base URL of the FastAPI service (e.g. `http://10.x.x.x:8000`)
- `API_KEY` - the same value you set for `API_KEY` in the service `.env`
- `KEY_TAG_PATHS` - list of Coater 1 tag paths to include in live context
- `HISTORIAN_PROVIDER` - your tag history provider name
- `ALARM_SOURCE_FILTER` - wildcard for filtering alarms to Coater 1

## Security note

The `API_KEY` is stored in the gateway script. Ignition project resources
are protected by Designer login; rotate the key periodically. Do not log
the key.
