"""Secure in-app updater for the packaged BrainAI macOS application.

The updater downloads the DMG and checksum published by GitHub Releases,
verifies both the checksum and the Developer ID signature of the contained
BrainAI.app, then starts a detached relauncher.  The relauncher waits for the
current process to shut down cleanly, swaps the bundles with rollback, and
opens the new version.
"""
from __future__ import annotations

import hashlib
import os
import plistlib
import shlex
import shutil
import ssl
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Callable, Optional

import certifi


APP_NAME = "BrainAI"
EXPECTED_TEAM_ID = "CHVK2XNJGD"
ProgressCb = Callable[[int, int], None]


class UpdateError(Exception):
    """A recoverable update failure suitable for showing to the user."""


def current_bundle_path() -> Path:
    """Return the running .app bundle, rejecting source-mode launches."""
    candidates = (Path(sys.executable), Path(__file__))
    for candidate in candidates:
        path = candidate.absolute()
        for parent in (path, *path.parents):
            if parent.name == f"{APP_NAME}.app":
                return parent
    raise UpdateError(
        "Self-update is available only when BrainAI is running from BrainAI.app."
    )


def _release_assets(release: dict) -> tuple[str, str]:
    """Return the BrainAI DMG and SHA-256 URLs from a GitHub release."""
    version = str(release.get("tag_name") or "").strip().lstrip("v")
    if not version:
        raise UpdateError("The latest release has no version tag.")
    expected_dmg = f"brainai-{version}.dmg".lower()
    expected_checksum = f"brainai-{version}.sha256".lower()
    assets: dict[str, str] = {}
    for asset in release.get("assets") or []:
        assets[str(asset.get("name") or "").lower()] = str(
            asset.get("browser_download_url") or ""
        )
    dmg_url = assets.get(expected_dmg, "")
    checksum_url = assets.get(expected_checksum, "")
    if not dmg_url:
        raise UpdateError("The latest release has no BrainAI DMG asset.")
    if not checksum_url:
        raise UpdateError("The latest release has no SHA-256 checksum asset.")
    return dmg_url, checksum_url


def _ssl_context() -> ssl.SSLContext:
    return ssl.create_default_context(cafile=certifi.where())


def _request(url: str) -> urllib.request.Request:
    return urllib.request.Request(
        url,
        headers={
            "User-Agent": "BrainAI-Updater",
            "Accept": "application/octet-stream",
        },
    )


def _download_text(url: str) -> str:
    with urllib.request.urlopen(_request(url), context=_ssl_context(), timeout=60) as response:
        return response.read(4096).decode("utf-8", errors="replace")


def download_file(url: str, dest: Path, progress: Optional[ProgressCb] = None) -> Path:
    """Download an asset and report byte progress when length is known."""
    with urllib.request.urlopen(_request(url), context=_ssl_context(), timeout=60) as response:
        total = int(response.headers.get("Content-Length") or 0)
        downloaded = 0
        with dest.open("wb") as output:
            while True:
                chunk = response.read(1024 * 1024)
                if not chunk:
                    break
                output.write(chunk)
                downloaded += len(chunk)
                if progress:
                    progress(downloaded, total)
        if total and downloaded != total:
            raise UpdateError(
                f"Incomplete download: received {downloaded} of {total} bytes."
            )
    return dest


def _verify_checksum(path: Path, checksum_text: str) -> None:
    expected = checksum_text.strip().split()[0].lower() if checksum_text.strip() else ""
    if len(expected) != 64 or any(ch not in "0123456789abcdef" for ch in expected):
        raise UpdateError("The release contains an invalid SHA-256 checksum.")
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != expected:
        raise UpdateError("The downloaded DMG failed SHA-256 verification.")


def _hdiutil_attach(dmg_path: Path) -> Path:
    try:
        output = subprocess.check_output(
            [
                "/usr/bin/hdiutil",
                "attach",
                "-nobrowse",
                "-noautoopen",
                "-readonly",
                "-plist",
                str(dmg_path),
            ],
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as exc:
        detail = exc.output.decode("utf-8", errors="replace").strip()
        raise UpdateError(f"Could not mount the update DMG: {detail[-240:]}") from exc
    plist = plistlib.loads(output)
    for entity in plist.get("system-entities", []):
        mount_point = entity.get("mount-point")
        if mount_point:
            return Path(mount_point)
    raise UpdateError("The update DMG mounted without a volume path.")


def _hdiutil_detach(mount_point: Path) -> None:
    subprocess.run(
        ["/usr/bin/hdiutil", "detach", "-quiet", str(mount_point)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def copy_app_from_dmg(dmg_path: Path, dest_dir: Path) -> Path:
    """Extract BrainAI.app from the mounted release DMG."""
    mount_point = _hdiutil_attach(dmg_path)
    try:
        source = mount_point / f"{APP_NAME}.app"
        if not source.is_dir():
            raise UpdateError("The update DMG does not contain BrainAI.app.")
        target = dest_dir / source.name
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(source, target, symlinks=True)
        return target
    finally:
        _hdiutil_detach(mount_point)


def verify_app_bundle(bundle: Path) -> None:
    """Require a valid BrainAI signature from the expected Apple team."""
    verify = subprocess.run(
        ["/usr/bin/codesign", "--verify", "--deep", "--strict", str(bundle)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if verify.returncode != 0:
        raise UpdateError(f"The downloaded app has an invalid signature: {verify.stdout.strip()[-240:]}")

    details = subprocess.run(
        ["/usr/bin/codesign", "-dv", "--verbose=4", str(bundle)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if details.returncode != 0 or f"TeamIdentifier={EXPECTED_TEAM_ID}" not in details.stdout:
        raise UpdateError("The downloaded app was not signed by the BrainAI developer.")

    gatekeeper = subprocess.run(
        ["/usr/sbin/spctl", "--assess", "--type", "execute", "--verbose=4", str(bundle)],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if gatekeeper.returncode != 0:
        raise UpdateError(
            f"Gatekeeper rejected the downloaded app: {gatekeeper.stdout.strip()[-240:]}"
        )


def _alert_script(message: str) -> str:
    safe = message.replace("\\", "\\\\").replace('"', '\\"')
    return f'display alert "BrainAI update failed" message "{safe}" as critical'


def _write_relauncher(current_bundle: Path, new_bundle: Path, pid: int, workdir: Path) -> Path:
    read_only = (
        "BrainAI.app could not be replaced. Move it to /Applications or "
        "~/Applications and make sure your account owns the app, then retry."
    )
    move_failed = (
        "The new app could not be installed, so the previous BrainAI version "
        "was restored. Retry or install the DMG manually."
    )
    exit_failed = "BrainAI did not exit in time. The update was not installed."
    script = (
        "#!/bin/sh\n"
        "# Generated BrainAI updater: wait, atomically swap, rollback, relaunch.\n"
        f"PID={pid}\n"
        f"OLD_APP={shlex.quote(str(current_bundle))}\n"
        f"NEW_APP={shlex.quote(str(new_bundle))}\n"
        f"WORK_DIR={shlex.quote(str(workdir))}\n"
        'BACKUP="${OLD_APP}.old.$$"\n'
        "for i in $(seq 1 120); do\n"
        '    if ! ps -p "$PID" >/dev/null 2>&1; then break; fi\n'
        "    sleep 0.5\n"
        "done\n"
        'if ps -p "$PID" >/dev/null 2>&1; then\n'
        f"    /usr/bin/osascript -e {shlex.quote(_alert_script(exit_failed))} >/dev/null 2>&1\n"
        '    rm -rf "$WORK_DIR" 2>/dev/null\n'
        '    rm -f "$0"\n'
        "    exit 1\n"
        "fi\n"
        'if ! mv "$OLD_APP" "$BACKUP" 2>/dev/null; then\n'
        f"    /usr/bin/osascript -e {shlex.quote(_alert_script(read_only))} >/dev/null 2>&1\n"
        '    [ -d "$OLD_APP" ] && /usr/bin/open "$OLD_APP"\n'
        '    rm -rf "$WORK_DIR" 2>/dev/null\n'
        '    rm -f "$0"\n'
        "    exit 1\n"
        "fi\n"
        'if ! mv "$NEW_APP" "$OLD_APP" 2>/dev/null; then\n'
        '    mv "$BACKUP" "$OLD_APP" 2>/dev/null\n'
        f"    /usr/bin/osascript -e {shlex.quote(_alert_script(move_failed))} >/dev/null 2>&1\n"
        '    [ -d "$OLD_APP" ] && /usr/bin/open "$OLD_APP"\n'
        '    rm -rf "$WORK_DIR" 2>/dev/null\n'
        '    rm -f "$0"\n'
        "    exit 1\n"
        "fi\n"
        'rm -rf "$BACKUP" "$WORK_DIR" 2>/dev/null\n'
        '/usr/bin/open "$OLD_APP"\n'
        'rm -f "$0"\n'
    )
    descriptor, path = tempfile.mkstemp(prefix="brainai-updater-", suffix=".sh")
    os.close(descriptor)
    result = Path(path)
    result.write_text(script, encoding="utf-8")
    result.chmod(0o700)
    return result


def install_update(release: dict, progress: Optional[ProgressCb] = None) -> None:
    """Prepare a verified update and launch the detached bundle swapper."""
    current = current_bundle_path()
    dmg_url, checksum_url = _release_assets(release)
    workdir = Path(tempfile.mkdtemp(prefix="brainai-update-"))
    dmg_path = workdir / "update.dmg"
    script: Optional[Path] = None
    try:
        checksum = _download_text(checksum_url)
        download_file(dmg_url, dmg_path, progress=progress)
        _verify_checksum(dmg_path, checksum)
        new_bundle = copy_app_from_dmg(dmg_path, workdir)
        verify_app_bundle(new_bundle)
        script = _write_relauncher(current, new_bundle, os.getpid(), workdir)
        subprocess.Popen(
            ["/bin/sh", str(script)],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
    except UpdateError:
        if script is not None:
            script.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    except Exception as exc:
        if script is not None:
            script.unlink(missing_ok=True)
        shutil.rmtree(workdir, ignore_errors=True)
        raise UpdateError(str(exc)) from exc
