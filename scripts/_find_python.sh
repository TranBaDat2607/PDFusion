# Shared interpreter resolution for the POSIX launchers. Sourced, not run.
#
# Same order as `sidecar.rs:locate_python`, and for the same reason: Tauri's
# beforeBundleCommand spawns a bare `sh` that inherits none of a conda
# activation, so `pnpm tauri build` arrives here with no `python` from the
# project's environment on PATH. `PDFUSION_PYTHON` is the variable that
# survives however the shell was launched.
#
# Sets PDFUSION_PY on success; exits 1 with an explanation otherwise.
pdfusion_find_python() {
    if [ -n "${PDFUSION_PYTHON:-}" ] && [ -x "$PDFUSION_PYTHON" ]; then
        PDFUSION_PY="$PDFUSION_PYTHON"
        return 0
    fi

    for dist in anaconda3 miniconda3 miniforge3; do
        for env_name in pdfusion pdfusion-env; do
            candidate="$HOME/$dist/envs/$env_name/bin/python"
            if [ -x "$candidate" ]; then
                PDFUSION_PY="$candidate"
                return 0
            fi
        done
    done

    for name in python3 python; do
        if command -v "$name" >/dev/null 2>&1; then
            PDFUSION_PY="$(command -v "$name")"
            return 0
        fi
    done

    echo "No Python found. Activate the pdfusion environment, or set" >&2
    echo "PDFUSION_PYTHON to that environment's python." >&2
    exit 1
}
