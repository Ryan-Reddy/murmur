# Murmur

Reads any selected text aloud, anywhere on Windows, fully offline. Powered by
[Kokoro-82M](https://github.com/thewh1teagle/kokoro-onnx) — nothing ever leaves
your machine.

Voice: 70% `bf_emma` + 30% `af_nicole` (British, sweet, a little raspy).
Tweak the `BLEND` constant in `murmur.py` to taste.

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Alt+Y` | Read the current selection (press again to read a new one) |
| `Ctrl+Alt+S` | Stop (or click the on-screen pill) |
| `Ctrl+Alt+Up` / `Down` | Faster / slower |

While reading, a small pill at the bottom of the screen shows the sentence
being spoken — click it to stop. The tray icon turns green while speaking.

## Run

```
venv\Scripts\python.exe murmur.py
```

Model files live in `models/` (`kokoro-v1.0.onnx` + `voices-v1.0.bin`, from the
[kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)).

## Use in your own projects

The engine lives in `speaker.py` with no UI dependencies — just
`kokoro-onnx` + `sounddevice` and the two model files:

```python
from speaker import Speaker

s = Speaker("models/kokoro-v1.0.onnx", "models/voices-v1.0.bin")
s.speak("Hello there.")   # non-blocking, streams sentence by sentence
s.stop()
```

Optional `on_state(speaking)` / `on_sentence(text)` callbacks give you
progress feedback, and `blend=` / `speed=` customize the voice.

## Voice auditions

`audition.py` / `audition2.py` regenerate the voice comparison samples in `samples/`.
