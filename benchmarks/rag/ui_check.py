"""Drive the real chat UI in headless Chromium against a real sidecar.

    pip install playwright && python -m playwright install chromium
    python benchmarks/rag/ui_check.py <out-dir> <some.pdf>

The Vite dev build and the sidecar are the real ones; only the Tauri shell is
stood in for, the way `@tauri-apps/api/mocks` does: the IPC bridge announces
the sidecar and hands over a PDF to open (`initial_file_argument`). It indexes
the PDF, asks a question and a follow-up (watching the streamed draft), rates
the answer, clicks a citation and changes Settings → Chat → "Read a PDF
whole", leaving a screenshot of each step in <out-dir>. The data root is a
throwaway under <out-dir> with a copy of the real `config.toml`, so it asks
the configured answer model (a few cents) and never touches `pdfusion.db`.
It is the manual check `CLAUDE.md` asks for where vitest (node, no DOM)
doesn't reach.
"""

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.sync_api import sync_playwright

WT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(WT / "src"))
from desktop_pdf_translator.utils.paths import appdata_dir  # noqa: E402

# Absolute, so the sidecar (which runs in ROOT) and Chromium read the same
# paths this script means.
OUT = Path(sys.argv[1]).expanduser().resolve()
PDF = str(Path(sys.argv[2]).expanduser().resolve())
ROOT = OUT / "root"
REAL_ROOT = appdata_dir().resolve()
# Never a directory that already holds something: this one is about to hold a
# throwaway data root, and "the data root" or "the checkout" are easy slips.
if OUT.exists() and any(OUT.iterdir()):
    sys.exit(f"{OUT} is not empty; give ui_check a new directory")
if OUT in (REAL_ROOT, WT) or REAL_ROOT in OUT.parents or OUT in REAL_ROOT.parents:
    sys.exit(f"{OUT} overlaps the real data root or the repository")
ROOT.mkdir(parents=True)
shutil.copy(REAL_ROOT / "config.toml", ROOT / "config.toml")

sidecar = subprocess.Popen(
    [sys.executable, "-m", "desktop_pdf_translator.api.server"], cwd=ROOT,
    env={**os.environ, "PDFUSION_DATA_DIR": str(ROOT), "PDFUSION_DEV_ORIGINS": "1",
         "PYTHONPATH": str(WT / "src")},
    stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
)
# Its own process group: `pnpm dev` starts vite as a grandchild, and a vite
# left holding port 1420 breaks the next `pnpm tauri dev` (or this script,
# which would then drive the stale one).
vite = subprocess.Popen(["pnpm", "dev", "--port", "1420", "--strictPort"], cwd=WT / "desktop",
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def stop(proc, group=False):
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM) if group else proc.terminate()
        proc.wait(timeout=20)
    except (subprocess.TimeoutExpired, ProcessLookupError):
        os.killpg(proc.pid, signal.SIGKILL) if group else proc.kill()
try:
    port, token = re.search(r"READY port=(\d+) token=(\S+)", sidecar.stdout.readline()).groups()
    # Nothing reads the pipe after READY; closing it keeps a chatty native
    # library from filling it and blocking the sidecar.
    sidecar.stdout.close()
    for _ in range(150):
        try:
            if httpx.get("http://localhost:1420", timeout=1).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.2)

    mock = """
    (() => {
      const callbacks = {};
      const listeners = {};
      let next = 1;
      const emit = (event, payload) => (listeners[event] || []).forEach(id =>
        callbacks[id] && callbacks[id]({ event, payload, id }));
      window.__emit = emit;
      window.__TAURI_INTERNALS__ = {
        metadata: { currentWindow: { label: "main" }, currentWebview: { windowLabel: "main", label: "main" } },
        transformCallback(cb, once) { const id = next++; callbacks[id] = cb; return id; },
        unregisterCallback(id) { delete callbacks[id]; },
        convertFileSrc(p) { return p; },
        async invoke(cmd, args) {
          if (cmd === "plugin:event|listen") {
            (listeners[args.event] = listeners[args.event] || []).push(args.handler);
            if (args.event === "sidecar://ready")
              setTimeout(() => emit("sidecar://ready", { port: %PORT%, token: "%TOKEN%" }), 50);
            return args.handler;
          }
          if (cmd === "initial_file_argument") return "%PDF%";
          return null;
        },
      };
      window.__TAURI_EVENT_PLUGIN_INTERNALS__ = { unregisterListener() {} };
    })();
    """.replace("%PORT%", port).replace("%TOKEN%", token).replace("%PDF%", PDF)

    shots = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1500, "height": 950})
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.add_init_script(mock)
        page.goto("http://localhost:1420")

        def shot(name):
            path = OUT / f"{len(shots) + 1:02d}-{name}.png"
            page.screenshot(path=str(path))
            shots.append(path.name)

        page.get_by_role("button", name="Chat").first.wait_for(timeout=60_000)
        time.sleep(2)
        shot("pdf-open")
        if page.get_by_role("button", name="Chat").first.get_attribute("aria-pressed") != "true":
            page.get_by_role("button", name="Chat").first.click()
        box = page.get_by_placeholder("Ask anything about this document…")
        box.wait_for(timeout=240_000)
        shot("indexed")

        def ask(question, name):
            box.fill(question)
            box.press("Enter")
            # The streamed draft shows before the checked answer replaces it.
            saw_draft = False
            deadline = time.time() + 120
            while time.time() < deadline:
                if page.locator("svg.animate-pulse").count():
                    saw_draft = True
                    shot(f"{name}-streaming")
                    break
                if not page.get_by_role("button", name="Send").is_disabled() and box.input_value() == "":
                    if page.locator("text=Sources").count():
                        break
                time.sleep(0.05)
            page.get_by_title(re.compile("Go to page")).last.wait_for(timeout=120_000)
            time.sleep(1)
            shot(name)
            return saw_draft

        drafts = [ask("What was revenue growth in Europe?", "answer"),
                  ask("And in Asia-Pacific?", "follow-up")]
        page.get_by_title("Wrong or unhelpful answer").last.click()
        time.sleep(1.5)
        shot("rated")
        # A citation link jumps the viewer, never navigates the webview.
        page.get_by_title(re.compile("Go to page")).last.click()
        time.sleep(1)
        shot("jumped")
        # Settings → Chat → "Read a PDF whole": the budget a user can change.
        page.get_by_role("button", name="Settings").click()
        page.get_by_role("tab", name="Chat").click()
        page.locator("#whole-document").click()
        page.get_by_role("option", name=re.compile("~40 pages")).click()
        time.sleep(1.5)
        shot("settings-whole-document")
        browser.close()

    api = httpx.Client(base_url=f"http://127.0.0.1:{port}", headers={"Authorization": f"Bearer {token}"})
    docs = api.get("/rag/documents").json()["documents"]
    messages = api.get(f"/rag/document/{docs[0]['document_id']}/messages").json()["messages"]
    print(json.dumps({
        "screenshots": shots,
        "streamed_draft_seen": drafts,
        "saved": [(m["role"], m["text"][:60], m.get("rating")) for m in messages],
        "follow_up_search_query": messages[-1]["answer"].get("search_query"),
        "whole_document_tokens_saved": api.get("/config").json()["rag"]["whole_document_tokens"],
        "page_errors": errors,
    }, indent=1, ensure_ascii=False))
finally:
    stop(vite, group=True)
    stop(sidecar)
