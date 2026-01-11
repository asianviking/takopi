# Discord Transport Plan

## Can This Be Done Without Modifying Takopi Core?

### Text Transport: Yes, fully self-contained

The Discord text transport can be implemented entirely within a `takopi/discord/` module without any changes to takopi core. The existing architecture provides everything needed:

| What Discord Needs | What Takopi Provides | Location |
|--------------------|---------------------|----------|
| Send/edit/delete messages | `Transport` protocol | `transport.py` |
| Plugin registration | Entry point system | `pyproject.toml` |
| Setup & configuration | `TransportBackend` protocol | `transports.py` |
| Project/branch context | `RunContext` dataclass | `context.py` |
| Message parsing & directives | `runtime.resolve_message()` | `transport_runtime.py` |
| Engine execution | `_run_engine()` pattern | (copied from telegram) |
| Session resume tokens | `ResumeToken` + state store | `model.py` + custom state |
| Settings schema | Pydantic models | `settings.py` (minor addition) |

The only change needed to takopi core is adding Discord settings to `settings.py` - this is the standard pattern for new transports.

### Voice Transport: Mostly self-contained, but...

Voice is more complex. Here's the breakdown:

| Component | Self-contained? | Notes |
|-----------|-----------------|-------|
| Join/leave voice channels | **Yes** | Discord API, no core changes |
| Record audio | **Yes** | Pycord `start_recording()` |
| STT (speech-to-text) | **Yes** | External API call in discord module |
| Send text to engine | **Yes** | Same as text transport |
| TTS (text-to-speech) | **Mostly** | External API, but see below |
| Play audio in channel | **Yes** | Discord API |

**Potential issue: Streaming responses**

The current flow is:
1. User sends text → engine processes → returns complete response → transport sends

For voice, the ideal flow would be:
1. User speaks → STT → engine processes → **stream chunks** → TTS each chunk → play

The existing `Runner` interface returns a complete response. For low-latency voice, we'd want streaming. Options:

1. **Without core changes**: Wait for full response, then TTS and play. Works but has latency (user waits for full response before hearing anything).

2. **With core changes**: Add streaming support to `Runner` protocol. Better UX but requires modifying `runner.py`.

**Recommendation**: Start with option 1 (no core changes). The latency is acceptable for most use cases. Streaming can be added later as an enhancement.

---

## Overview

This document outlines the implementation plan for a Discord transport for takopi, mapping Discord's structure to takopi's project/branch/session model:

| Discord Concept | Takopi Concept |
|-----------------|----------------|
| Category | Project |
| Channel (within category) | Branch |
| Thread (within channel) | AI Session |
| Voice Channel | Real-time voice AI session |

## Feasibility Assessment

### Text-Based Features: Fully Feasible

The core text-based functionality is straightforward to implement using [Pycord](https://docs.pycord.dev/) (a maintained discord.py fork):

- **Categories as Projects** - Discord categories are containers for channels; we can read the category name as the project context
- **Channels as Branches** - Text channels within a category can represent branches
- **Threads as Sessions** - Discord threads support session isolation with resume tokens, similar to Telegram topics

### Voice Features: Feasible with Complexity

Voice chat integration **is possible** but requires additional components:

| Feature | Feasibility | Notes |
|---------|-------------|-------|
| Bot joins voice channel | **Yes** | Native Discord API support via [VoiceChannel.connect()](https://discordjs.guide/voice/voice-connections) |
| Bot plays audio (TTS) | **Yes** | Well-supported via FFmpeg + opus codec |
| Bot receives/records audio | **Yes** | Supported in [Pycord](https://guide.pycord.dev/voice/receiving) with `start_recording()` |
| Real-time STT | **Yes** | Requires external API (Whisper, Gladia, AssemblyAI, etc.) |
| Real-time conversation | **Yes** | Requires integration with STT + LLM + TTS pipeline |

**Reference implementations:**
- [Discord-VC-LLM](https://github.com/Eidenz/Discord-VC-LLM) - Full voice chat LLM bot
- [SeaVoice](https://voice.seasalt.ai/discord/) - Commercial STT/TTS Discord bot
- [Gladia tutorial](https://www.gladia.io/blog/how-to-build-a-voice-to-text-discord-bot-with-gladia-real-time-transcription-api) - Real-time transcription guide

**Voice dependencies:**
- FFmpeg (audio processing)
- PyNaCl (voice encryption)
- Opus codec
- External STT API (OpenAI Whisper API, Gladia, AssemblyAI, etc.)
- External TTS API (OpenAI TTS, ElevenLabs, etc.) or local (pyttsx3, edge-tts)

## Architecture

### Module Structure

```
src/takopi/discord/
├── __init__.py
├── backend.py          # TransportBackend implementation (entry point)
├── bridge.py           # Transport implementation (send/edit/delete)
├── bot.py              # Discord bot client and event handlers
├── context.py          # Category/channel → project/branch mapping
├── state.py            # Thread/session state persistence
├── voice/
│   ├── __init__.py
│   ├── handler.py      # Voice channel join/leave logic
│   ├── stt.py          # Speech-to-text integration
│   └── tts.py          # Text-to-speech integration
└── config.py           # Configuration dataclasses
```

### Context Mapping Logic

```python
def resolve_context(channel: discord.TextChannel | discord.Thread) -> RunContext:
    """
    Map Discord structure to takopi context.

    Category name → project
    Channel name → branch
    Thread → session (same project/branch as parent channel)
    """
    if isinstance(channel, discord.Thread):
        parent = channel.parent
        category = parent.category
    else:
        parent = channel
        category = channel.category

    return RunContext(
        project=category.name if category else None,
        branch=parent.name if parent else None,
    )
```

### Voice Flow

```
User joins voice channel
        ↓
Bot detects via on_voice_state_update event
        ↓
Bot joins the same voice channel
        ↓
Bot starts recording (Pycord start_recording)
        ↓
Audio chunks → STT API → text
        ↓
Text → takopi engine → response
        ↓
Response → TTS API → audio
        ↓
Bot plays audio in voice channel
        ↓
(loop until user leaves or says "bye"/disconnect command)
```

## Configuration Schema

```toml
[transports.discord]
bot_token = "..."
guild_id = 123456789          # Server ID

[transports.discord.context]
# How to map Discord structure to projects
category_as_project = true    # Category name = project
channel_as_branch = true      # Channel name = branch
# Alternatively, explicit mappings:
# [transports.discord.context.mappings]
# "category-name" = "actual-project-name"

[transports.discord.voice]
enabled = false               # Voice features disabled by default
auto_join = true              # Auto-join when user joins voice channel
stt_provider = "openai"       # "openai", "gladia", "assemblyai", "whisper-local"
tts_provider = "openai"       # "openai", "elevenlabs", "edge-tts"
trigger_word = ""             # Empty = always listen, or e.g. "hey tako"
silence_threshold_ms = 1500   # Silence duration to trigger processing

[transports.discord.voice.openai]
# Uses OPENAI_API_KEY from environment
model = "whisper-1"           # STT model
voice = "alloy"               # TTS voice

[transports.discord.voice.elevenlabs]
api_key = "..."               # Or ELEVENLABS_API_KEY env var
voice_id = "..."
```

## Implementation Phases

### Phase 1: Core Text Transport

Implement basic Discord transport without voice:

1. **Backend setup** (`backend.py`)
   - Implement `TransportBackend` protocol
   - `check_setup()` - verify bot token, guild access
   - `interactive_setup()` - guide user through bot creation
   - `build_and_run()` - start the bot

2. **Bot client** (`bot.py`)
   - Connect to Discord gateway
   - Handle `on_message` events
   - Route messages to takopi engine

3. **Transport bridge** (`bridge.py`)
   - Implement `Transport` protocol
   - `send()` - send message to channel/thread
   - `edit()` - edit existing message
   - `delete()` - delete message

4. **Context resolution** (`context.py`)
   - Map category → project
   - Map channel → branch
   - Handle DMs (no project/branch context)

5. **State persistence** (`state.py`)
   - Track thread → session mappings
   - Store resume tokens per thread
   - Similar to `telegram/topic_state.py`

6. **Configuration** (`config.py`, `settings.py`)
   - Add `DiscordTransportSettings` to settings schema
   - Define configuration dataclasses

7. **Plugin registration** (`pyproject.toml`)
   - Add entry point: `discord = "takopi.discord.backend:BACKEND"`

### Phase 2: Thread/Session Management

Enhance with thread-based sessions:

1. **Thread creation** - Create threads for new sessions
2. **Thread switching** - Switch context when user moves between threads
3. **Session resume** - Resume sessions when returning to a thread
4. **Context directives** - Support `/project` and `/branch` commands in threads

### Phase 3: Voice Integration

Add voice channel support:

1. **Voice handler** (`voice/handler.py`)
   - Detect user joining voice channels
   - Join/leave voice channels
   - Manage voice client lifecycle

2. **STT integration** (`voice/stt.py`)
   - Record audio from voice channel
   - Send to STT provider
   - Return transcribed text

3. **TTS integration** (`voice/tts.py`)
   - Convert response text to audio
   - Play audio in voice channel

4. **Voice session management**
   - Determine project context from voice channel's category
   - Maintain conversation state during voice session
   - Handle multi-user scenarios (who is the bot talking to?)

### Phase 4: Polish & Edge Cases

1. **Permissions handling** - Graceful errors for missing permissions
2. **Rate limiting** - Respect Discord rate limits
3. **Reconnection** - Handle disconnects gracefully
4. **Multi-guild support** - Support multiple Discord servers
5. **Slash commands** - Add `/project`, `/branch`, `/session` commands

## Key Differences from Telegram

| Aspect | Telegram | Discord |
|--------|----------|---------|
| Structure | Flat (topics in supergroups) | Hierarchical (categories/channels/threads) |
| Context source | Topic name or directives | Category + channel name |
| Voice | Voice messages (async) | Voice channels (real-time) |
| Threading | Forum topics | Native threads |
| Auth | Chat ID allowlist | Guild membership + roles |

## Dependencies to Add

```toml
[project.optional-dependencies]
discord = [
    "py-cord>=2.6",           # Discord API (Pycord fork with voice receive)
    "pynacl>=1.5",            # Voice encryption
    "ffmpeg-python>=0.2",     # Audio processing (optional, for voice)
]
```

## Open Questions

1. **Multi-user voice** - How to handle multiple users in a voice channel? Options:
   - Only respond to the user who triggered the bot
   - Use voice activity detection to identify speakers
   - Use trigger word to know when someone is addressing the bot

2. **Voice channel context** - Should voice channels in a category share the same project context as text channels?

3. **Session persistence** - Should voice sessions create persistent threads for the transcript?

4. **Engine support** - Do all engines support streaming responses well for voice?

## Resources

- [Pycord Documentation](https://docs.pycord.dev/)
- [Pycord Voice Receiving Guide](https://guide.pycord.dev/voice/receiving)
- [Discord.js Voice Connections](https://discordjs.guide/voice/voice-connections) (JS reference)
- [Discord-VC-LLM](https://github.com/Eidenz/Discord-VC-LLM) - Reference implementation
- [AssemblyAI Discord Voice Bot Tutorial](https://www.assemblyai.com/blog/build-a-discord-voice-bot-to-add-chatgpt-to-your-voice-channel)
