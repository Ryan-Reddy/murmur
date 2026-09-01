# Yappy

Reads any selected text aloud, anywhere on Windows, fully offline. Powered by
[Kokoro-82M](https://github.com/thewh1teagle/kokoro-onnx) — nothing ever leaves
your machine.

Voice: 70% `bf_emma` + 30% `af_nicole` (British, sweet, a little raspy).
Tweak the `BLEND` constant in `yappy.py` to taste.

## Hotkeys

| Keys | Action |
|---|---|
| `Ctrl+Alt+R` | Read the current selection (press again to read a new one) |
| `Ctrl+Alt+S` | Stop |
| `Ctrl+Alt+Up` / `Down` | Faster / slower |

## Run

```
venv\Scripts\python.exe yappy.py
```

Model files live in `models/` (`kokoro-v1.0.onnx` + `voices-v1.0.bin`, from the
[kokoro-onnx releases](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)).

## Voice auditions

`audition.py` / `audition2.py` regenerate the voice comparison samples in `samples/`.
