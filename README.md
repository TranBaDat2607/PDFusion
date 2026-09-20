<div align="center">

# PDFusion

**Translate a PDF and keep it looking like a PDF.**

Columns, tables, figures and page breaks stay where the author put them — then
ask the document questions and get answers with page citations.

[![Release](https://img.shields.io/github/v/release/TranBaDat2607/PDFusion?color=2ea043&label=download)](https://github.com/TranBaDat2607/PDFusion/releases/latest)
[![CI](https://img.shields.io/github/actions/workflow/status/TranBaDat2607/PDFusion/ci.yml?branch=main&label=CI)](https://github.com/TranBaDat2607/PDFusion/actions/workflows/ci.yml)
[![Platforms](https://img.shields.io/badge/platforms-Windows%20%7C%20Linux-informational)](#download)
[![License](https://img.shields.io/github/license/TranBaDat2607/PDFusion?color=blue)](LICENSE)

</div>

<!-- Screenshots go here once the UI is settled: drop them in docs/images/ and
     link them from this block. -->

---

Most translators hand back a wall of text. PDFusion rebuilds the page: the
translated text is laid back into the original layout, so a paper stays a paper
and a form stays a form. It runs entirely on your machine, and **the first
translation works with no account, no API key and no internet** — an offline
English→Vietnamese engine ships inside the installer.

- **Nothing leaves your computer** unless you choose a cloud model.
- **Bring your own model** — OpenAI, Gemini, Claude, or anything
  OpenAI-compatible running on `localhost`.
- **Ask the document things** — answers quote the pages they came from.

## Download

**[⬇ Get the latest release](https://github.com/TranBaDat2607/PDFusion/releases/latest)**

| Platform | File | Install |
|---|---|---|
| Windows 10/11 (x64) | `PDFusion_<version>_x64-setup.exe` | Run it. Installs per user — no admin rights, nothing in `Program Files`. |
| Debian / Ubuntu (x64) | `PDFusion_<version>_amd64.deb` | `sudo apt install ./PDFusion_<version>_amd64.deb` |

Two things everyone hits on first install:

- **Windows SmartScreen warns you.** The installer is unsigned — a code-signing
  certificate costs money the project doesn't have yet. Choose *More info* →
  *Run anyway*.
- **Linux needs webkit2gtk 4.1**, not 4.0. On Ubuntu:
  `sudo apt install libwebkit2gtk-4.1-0 libgtk-3-0`.

The download is large (several hundred MB) because the translation engine —
layout models, fonts and the offline language pack, about 290 MB of it — ships
inside so the app works on first run without downloading anything.

> **macOS** builds from source but isn't packaged. An unsigned, un-notarized
> `.dmg` is refused by Gatekeeper with no useful way past it, so shipping one
> would be worse than shipping none ([#69](https://github.com/TranBaDat2607/PDFusion/issues/69)).

## Quick start

1. **Open a PDF** — `Ctrl+O`, or drop the file onto the window.
2. **Pick your languages** in the *From* and *To* boxes. *To* defaults to
   Vietnamese.
3. *(Optional)* **Narrow the range** in the *Pages* box — `1-10, 14, 22-` all
   work. Up to 50 pages per run.
4. **Press Translate.** The translated pages appear beside the original as they
   finish; you can cancel mid-run.
5. **Press Save PDF…** to keep it.

> **Step 5 is not optional.** Until you save, the translated file lives in a
> temporary folder and is cleaned up. The app never writes over the PDF you
> opened.

Untranslated pages are copied through from the original, so what you save is
always the whole document.

## Translation services

Pick one in the toolbar. Only the first works offline.

| Service | API key | Translates | Notes |
|---|---|---|---|
| **Argos Translate** | not needed | English → Vietnamese | Runs on your machine. The default, and what ships in the installer. |
| **OpenAI** | yes | any supported pair | Also speaks to any OpenAI-compatible server. |
| **Google Gemini** | yes | any supported pair | |
| **Anthropic Claude** | yes | any supported pair | Also speaks to Claude-compatible servers. |

Languages offered: Vietnamese, English, Japanese, Chinese (Simplified), Chinese
(Traditional), with auto-detection for the source. PDFusion is
**Vietnamese-first, not Vietnamese-only** — with an API key it translates
between any of these pairs.

Add keys in **Settings** — they're encrypted before they touch disk. If you
prefer a file, PDFusion also reads a `.env` from its data folder (see
[Where your data lives](#where-your-data-lives)):

```env
OPENAI_API_KEY=...
GEMINI_API_KEY=...
ANTHROPIC_API_KEY=...
```

If you select a cloud service with no key configured, PDFusion falls back to the
offline engine rather than failing.

When a cloud model is selected, the toolbar shows an estimate of the tokens the
run will use, so a 200-page document doesn't surprise you.

## Use your own model

The **OpenAI** and **Claude** tabs in Settings each have an **Endpoint** field,
so PDFusion can translate with a model running on your own machine, or through a
proxy that speaks one of those APIs. Leave it blank to use the provider itself.

| Server | Settings tab | Endpoint | API key | Model |
|---|---|---|---|---|
| [Ollama](https://ollama.com) | OpenAI | `http://localhost:11434/v1` | any text, e.g. `ollama` | a name from `ollama list` |
| Ollama (recent versions) | Claude | `http://localhost:11434` | any text | a name from `ollama list` |
| [LM Studio](https://lmstudio.ai) | OpenAI | `http://localhost:1234/v1` | any text | the model identifier LM Studio shows |

Then pick that service in the toolbar. Worth knowing:

- **Enter the key together with the endpoint.** A saved key is only ever sent to
  the endpoint it was saved for, so changing the endpoint asks for the key
  again. Local servers ignore the key, but the field can't be empty. An
  `OPENAI_API_KEY` or `ANTHROPIC_API_KEY` from your environment is only ever
  used with the provider itself.
- **Save checks the model with the server first.** If the server isn't running
  yet, Save shows the error and offers **Save anyway**.
- **The model field takes any name.** The list beside it only makes suggestions.
- Chat writes its answers with the same service and endpoint.

## Chat with your document

Open the chat panel and ask about the PDF you have loaded. Retrieval combines
keyword and semantic search, and every answer lists the pages it drew on, so you
can check it. Each document keeps its own conversation, which you can clear.

Chat answers from exactly one document — the one you have open — so it never
blends two papers together.

**First use downloads about 470 MB** — a multilingual embedding model, fetched
once into `~/.cache/huggingface`. Everything after that is local. This is the
only part of PDFusion that needs the internet when you're using the offline
translator.

## Keyboard shortcuts

| | |
|---|---|
| `Ctrl+O` | Open a PDF |
| `Ctrl+F` | Find in document |
| `F3` / `Shift+F3` | Next / previous match |
| `Ctrl` `+` / `-` / `0` | Zoom in, out, reset |

## Where your data lives

Everything stays on your machine.

| | Windows | Linux | macOS |
|---|---|---|---|
| Settings, cache, chat index | `%LOCALAPPDATA%\PDFusion` | `~/.local/share/PDFusion` | `~/Library/Application Support/PDFusion` |
| Logs | `…\PDFusion\logs` | `…/PDFusion/logs` | `…/PDFusion/logs` |
| API keys protected by | DPAPI, scoped to your account | Secret Service (gnome-keyring, KWallet) | Keychain |

Translations are cached, so re-translating the same document is quick. You can
clear the cache from Settings.

**On Linux with no keyring running** — a bare tiling-WM setup, a container —
keys fall back to an obfuscated value in `config.toml` that anyone who can read
the file can recover. PDFusion says so in `app.log` rather than refusing to
start. Install and unlock `gnome-keyring` or `kwalletmanager` if that matters to
you.

## Troubleshooting

**Windows says the app is unsigned.** It is. *More info* → *Run anyway*. See
[Download](#download).

**The first translation is slow.** The engine loads its layout models on first
use. Later runs reuse them, and repeat documents come from the cache.

**Chat won't start.** It needs that one-time 470 MB model download — check your
connection and `app.log`.

**A PDF won't open or looks wrong.** Scanned pages with no text layer can't be
translated; there's no OCR step yet. Please
[open an issue](https://github.com/TranBaDat2607/PDFusion/issues) with the file
if you can share it.

**Where are the logs?** The boot screen has a *Show logs folder* button, which
opens the right directory for your platform.

## Building from source

See **[docs/development.md](docs/development.md)** for prerequisites, setup and
how to build an installer, and
**[docs/architecture-notes.md](docs/architecture-notes.md)** for why the app is
put together the way it is.

Issues and pull requests are welcome.

## License

[MIT](LICENSE).

Built on [BabelDOC](https://github.com/funstory-ai/BabelDOC) for
layout-preserving translation and [Argos Translate](https://www.argosopentech.com/)
for the offline engine.
