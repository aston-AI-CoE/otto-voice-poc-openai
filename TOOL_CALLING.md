# OpenAI Realtime Tool Calling — How It Works

## 1. How OpenAI Knows What Tools It Can Call

When the browser hits **Start**, it calls `POST /session` on our server. Our server immediately calls OpenAI's session creation endpoint and passes the tool definitions:

```python
# server.py — POST /session
async with httpx.AsyncClient() as client:
    resp = await client.post(
        "https://api.openai.com/v1/realtime/client_secrets",
        json={
            "session": {
                "model": "gpt-realtime",
                "instructions": PROMPT,   # Otto's system prompt
                "tools": [
                    {
                        "type": "function",
                        "name": "Bash",
                        "description": "Execute a bash command in a persistent shell session...",
                        "parameters": {
                            "type": "object",
                            "properties": {
                                "command": { "type": "string" },
                                "timeout": { "type": "number" }
                            },
                            "required": ["command"]
                        }
                    },
                    # ... BashOutput, KillBash
                ]
            }
        }
    )
```

OpenAI bakes the tool schemas into the session at creation time. The model knows about all three tools (`Bash`, `BashOutput`, `KillBash`) for the entire session lifetime — it cannot be changed mid-session.

The **`description` field is critical** — it's what the model reads to decide *when* to use a tool. The parameters schema tells it *what arguments to pass*. There's no other signal.

---

## 2. Connection Architecture

OpenAI never talks to our server directly. Two channels exist between the browser and OpenAI:

```
Browser                          OpenAI Realtime
───────                          ───────────────
WebRTC media channel   ────────▶  mic audio (continuous stream)
                       ◀────────  model voice output

WebRTC data channel    ────────▶  client events (session.update, response.create,
  ("oai-events")                                  conversation.item.create)
                       ◀────────  server events  (transcripts, tool calls, errors)
```

Our FastAPI server only enters the picture when a tool needs to be executed. The browser is the intermediary that connects OpenAI to our server.

---

## 3. Full Tool Call Flow

```
Browser                      OpenAI Realtime              Our Server (FastAPI)
────────                     ───────────────              ───────────────────

[1] User speaks into mic
    │
    │  WebRTC audio stream ──────────────────────▶
    │                             Whisper transcribes speech
    │                             Model decides: "I should call Bash"
    │                             Streams tool arguments token by token
    │                             ◀────────────────────────────────────
    │  datachannel fires:
    │  response.function_call_arguments.done
    │  {
    │    type: "response.function_call_arguments.done",
    │    name: "Bash",
    │    call_id: "call_abc123",
    │    arguments: '{"command":"df -h"}'
    │  }
    │
[2] Browser catches the event in onDataChannelMessage()
    Parses name + arguments
    Shows tool call in transcript UI
    │
    │  POST /session/{id}/tool ──────────────────────────────────────▶
    │  { name: "Bash", arguments: { command: "df -h" } }
    │                                                   Runs BashExecutor
    │                                                   .run("df -h")
    │  ◀──────────────────────────────────────────────────────────────
    │  { output: "Filesystem  Size  ...", exit_code: 0 }
    │
[3] Browser sends result back to OpenAI via datachannel:
    │
    │  conversation.item.create ──────────────────────▶
    │  {
    │    type: "conversation.item.create",
    │    item: {
    │      type: "function_call_output",
    │      call_id: "call_abc123",      ← must match OpenAI's call_id exactly
    │      output: '{"output":"...","exit_code":0}'
    │    }
    │  }
    │
    │  response.create ───────────────────────────────▶
    │  { type: "response.create" }      ← tells OpenAI "now respond verbally"
    │
[4]                          Model reads the tool output
                             Generates spoken response
                             Streams audio + transcript back
                             ◀────────────────────────────────────
    Browser receives:
    response.output_audio_transcript.delta  → updates transcript UI
    WebRTC audio ──────────────────────────▶ plays Otto's voice
```

---

## 4. The Client-Side Handler

```javascript
// client.html — onDataChannelMessage()
function onDataChannelMessage(ev) {
    const event = JSON.parse(ev.data);

    switch (event.type) {

        case 'response.function_call_arguments.done':
            handleToolCall(event);   // ← tool call intercepted here
            break;

        case 'response.output_audio_transcript.delta':
            // stream Otto's speech to transcript UI
            break;

        case 'conversation.item.input_audio_transcription.completed':
            // show user's speech in transcript UI
            break;
    }
}

async function handleToolCall(event) {
    const args = JSON.parse(event.arguments);

    // 1. Execute on our server
    const result = await fetch(`/session/${sessionId}/tool`, {
        method: 'POST',
        body: JSON.stringify({ name: event.name, arguments: args })
    }).then(r => r.json());

    // 2. Send result back to OpenAI
    dc.send(JSON.stringify({
        type: 'conversation.item.create',
        item: {
            type: 'function_call_output',
            call_id: event.call_id,     // ← OpenAI matches this to the original call
            output: JSON.stringify(result)
        }
    }));

    // 3. Tell OpenAI to continue speaking
    dc.send(JSON.stringify({ type: 'response.create' }));
}
```

---

## 5. The Server-Side Handler

```python
# server.py — POST /session/{session_id}/tool
@app.post("/session/{session_id}/tool")
async def execute_tool(session_id: str, request: Request):
    body = await request.json()
    tool_name = body["name"]       # "Bash" / "BashOutput" / "KillBash"
    args = body["arguments"]

    executor = sessions[session_id]["executor"]  # BashExecutor instance

    if tool_name == "Bash":
        output, exit_code = await executor.run(args["command"], timeout=30)
        return { "output": output, "exit_code": exit_code }

    elif tool_name == "BashOutput":
        output, exit_code = await executor.output(args["pid"])
        return { "output": output, "exit_code": exit_code }

    elif tool_name == "KillBash":
        killed = await executor.kill(args["pid"])
        return { "killed": killed }
```

The `BashExecutor` is imported directly from Otto's library (`/root/otto/library/bash`) — same class Otto's worker uses internally.

---

## 6. Key Rules

| Rule | Why |
|------|-----|
| `call_id` must round-trip exactly | OpenAI uses it to match `function_call_output` to the original tool call. Wrong ID = model confusion. |
| `response.create` must follow every tool result | OpenAI won't generate a response until explicitly told to. Without it, the conversation silently stops. |
| Tool definitions are set at session creation only | Cannot add/remove tools mid-session. Plan your tool set upfront. |
| `description` drives tool selection | The model has no other signal for when to call a tool. Vague descriptions = wrong tool calls. |
| Tool output must be a string | `output` in `function_call_output` is always a JSON string, not a raw object. Use `JSON.stringify(result)`. |
| OpenAI never calls our server | All routing goes Browser → datachannel event → our server → datachannel result → OpenAI. |
