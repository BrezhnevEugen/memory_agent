"""Native update prompt with release notes for the BrainAI menu-bar app."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleInformational,
    NSAlertThirdButtonReturn,
    NSApplication,
    NSBezelBorder,
    NSFont,
    NSImage,
    NSMakeRect,
    NSScrollView,
    NSTextView,
)


INSTALL = "install"
LATER = "later"
OPEN_RELEASE = "open_release"


def _clean_release_body(body: str, max_len: int = 2400) -> str:
    if not body:
        return ""
    in_fence = False
    lines: list[str] = []
    for line in body.strip().splitlines():
        if line.strip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if line.lstrip().startswith("#"):
            line = line.lstrip("# ").rstrip()
        if line:
            lines.append(line)
    result = "\n".join(lines).strip()
    if len(result) > max_len:
        result = result[:max_len].rstrip() + "…"
    return result


def show_update_dialog(
    current_version: str,
    latest_version: str,
    release_body: str = "",
    icon_path: Optional[Path] = None,
) -> str:
    """Show the install/later/release-page prompt on the main thread."""
    NSApplication.sharedApplication().activateIgnoringOtherApps_(True)
    alert = NSAlert.alloc().init()
    alert.setAlertStyle_(NSAlertStyleInformational)
    alert.setMessageText_(f"BrainAI {latest_version} is available")
    alert.setInformativeText_(f"You are currently using BrainAI {current_version}.")

    alert.addButtonWithTitle_("Install update")
    alert.addButtonWithTitle_("Later")
    alert.addButtonWithTitle_("Open release page")
    alert.buttons()[1].setKeyEquivalent_("\x1b")

    if icon_path is not None and Path(icon_path).exists():
        image = NSImage.alloc().initWithContentsOfFile_(str(icon_path))
        if image:
            alert.setIcon_(image)

    body = _clean_release_body(release_body)
    if body:
        scroll = NSScrollView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 200))
        scroll.setHasVerticalScroller_(True)
        scroll.setBorderType_(NSBezelBorder)
        scroll.setAutohidesScrollers_(True)
        text = NSTextView.alloc().initWithFrame_(NSMakeRect(0, 0, 420, 200))
        text.setEditable_(False)
        text.setRichText_(False)
        text.setString_(body)
        text.setFont_(NSFont.systemFontOfSize_(12))
        text.setTextContainerInset_((6, 6))
        scroll.setDocumentView_(text)
        alert.setAccessoryView_(scroll)

    response = alert.runModal()
    if response == NSAlertFirstButtonReturn:
        return INSTALL
    if response == NSAlertThirdButtonReturn:
        return OPEN_RELEASE
    return LATER
