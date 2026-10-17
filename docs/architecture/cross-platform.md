# Cross-platform

Windows, Linux and macOS (#69). The Python sidecar was already portable —
every dependency has a Linux and macOS wheel, and nothing under `src/` reached
for a Windows API except the key store. What was Windows-only was everything
*around* it: packaging, the data root, the build scripts and CI. Five rules
came out of fixing that, and each one is easy to undo by collapsing it back to
the platform in front of you.

**1. The data root is the platform's, and the shell and the sidecar agree by
construction.**

| Platform | Root |
|---|---|
| Windows | `%LOCALAPPDATA%\PDFusion`, then `~/AppData/Local/PDFusion` |
| macOS | `~/Library/Application Support/PDFusion` |
| Linux / other POSIX | `$XDG_DATA_HOME/PDFusion`, else `~/.local/share/PDFusion` |

Two implementations of that table exist — `utils/paths.py:_platform_data_dirs`
and `sidecar.rs:data_dir_candidates` — because either side can run without the
other. They are not what keeps them in step: the shell resolves the root, makes
it the sidecar's cwd, pre-creates the layout in it, **and exports it as
`PDFUSION_DATA_DIR`**, which `appdata_dir()` reads before anything else. The
Python table is the fallback for a sidecar run by hand. Keep the export, and
keep it on *both* spawn paths — the dev path runs from the repo root, so its
cwd is not the data root and `build_command` sets the variable explicitly.

Off Windows the literal `~/AppData/Local/PDFusion` is **not** a fallback root.
A from-source run predating #69 did write there, so `adopt_legacy_config`
carries a config across from it once; `pdfusion.db` deliberately does not come
along, because it is a live SQLite database with its own WAL and a byte copy of
one of those is how a records store gets corrupted. Its old location is named
in the log instead. A relative `XDG_DATA_HOME` is ignored rather than resolved,
per the spec and because the cwd it would resolve against *is* the directory
being chosen.

Each candidate in that list is resolved **on its own terms**, and
`_platform_data_dirs` is the Python mirror of `data_dir_candidates` in this
too: `_home_dir()` answers `None` rather than raising, exactly as the shell's
`home_dir()` returns an `Option`. Built as one expression instead, a home that
cannot be resolved — no `HOME`, no passwd entry — discarded the `%LOCALAPPDATA%`
candidate standing beside it, and the root fell through to the temp dir, where
config, keys and caches are lost on the next reboot.

**2. Off Windows the sidecar is not an `externalBin`.** PyInstaller's one-dir
bootloader resolves `_internal/` relative to its executable, so the two must
stay siblings after the bundler has moved them. On Windows they do:
`externalBin` lands at the install root and so does a `resources` glob. On
Linux `externalBin` goes to `/usr/bin` while resources go to
`/usr/lib/<product>`; on macOS it is `Contents/MacOS` vs `Contents/Resources`.
Either way the pair is split and the sidecar dies looking for
`libpython3.11.so`. So off Windows the whole one-dir tree ships as a single
resource directory (`desktop/src-tauri/sidecar/`), and
`sidecar.rs:BUNDLED_SIDECAR_FILENAME` is `sidecar/pdfusion-sidecar` rather than
the bare name. `ensure_executable` is the other half: a resource copied through
an archive or an unusual umask can arrive without `+x`, and a `Permission
denied` on spawn would otherwise read as "no sidecar" and send a *shipped*
install down the local-Python fallback.

**3. Packaging lives in `tauri.<platform>.conf.json`, not in the shared
config.** Tauri merges `tauri.windows.conf.json` / `tauri.linux.conf.json` /
`tauri.macos.conf.json` over `tauri.conf.json` for the target being built, and
each one carries that platform's `bundle.targets`, its `externalBin` /
`resources` arrangement and its `beforeBundleCommand`. The shared file now
carries none of those. This is not stylistic: `bundle.targets` cannot name a
target the host platform can't build, and `beforeBundleCommand` is a shell
command — `powershell … build-sidecar.ps1` on Windows, `sh ../build-sidecar.sh`
elsewhere.

**4. One build script, two launchers.** `scripts/build_sidecar.py` and
`scripts/fetch_offline_assets.py` decide *what* happens;
`build-sidecar.{ps1,sh}` and `fetch-offline-assets.{ps1,sh}` only resolve an
interpreter and hand over. The launchers exist because
`beforeBundleCommand` spawns a bare shell that inherits none of a conda
activation, so `pnpm tauri build` arrives with no project `python` on PATH —
`PDFUSION_PYTHON` is the variable that survives that, and the same order is
implemented in `sidecar.rs:locate_python`, `scripts/_find_python.sh` and the
two `.ps1` files. Don't move build logic back into a launcher: two copies of it
is how the platforms drift.

**5. `windows-sys` is target-gated** (`[target.'cfg(windows)'.dependencies]`),
so a Linux or macOS build never resolves or compiles it. The Job Object
confinement and `CREATE_NO_WINDOW` that use it were already behind
`#[cfg(windows)]`; the dependency was not. Note the consequence: **there is no
Job Object equivalent off Windows.** The supervisor poll and
`RunEvent::ExitRequested` still stop the sidecar on a normal exit, but a
`SIGKILL` of the shell orphans the child. `prctl(PR_SET_PDEATHSIG)` on Linux
would be the analogue; nothing implements it yet.

Two smaller things that were Windows-shaped and are not any more.
`sidecar.rs:conda_python_candidates` looks for `envs/<name>/bin/python` off
Windows and `envs/<name>/python.exe` on it, and knows `miniforge3` as well as
`anaconda3`/`miniconda3` — the old code looked only for `python.exe` under
`%USERPROFILE%`, so on Linux and macOS it always missed and fell through to
whatever `python` was on `$PATH`. And the `cargo test` cases that needed a
child process to exit with status 3 spelled it `cmd /C exit 3`; they now go
through one `exits_with_code_3()` helper.

**What is still unverified.** Everything above compiles and its pure parts are
tested, and CI gates Linux. Nobody has run `pnpm tauri build` on Linux to
completion, opened the resulting `.deb`, or touched macOS at all — so
the resource-directory layout in rule 2 in particular is reasoned from the
bundler's source, not observed. The `frozen-sidecar` workflow (dispatch-only,
now a Windows + Linux matrix) is what settles the PyInstaller half.
