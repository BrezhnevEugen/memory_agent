"""Render the 🧠 emoji into BrainAI.icns (run with the bundled python that has pyobjc)."""
import subprocess, sys, tempfile, pathlib
from AppKit import NSImage, NSFont, NSColor, NSBitmapImageRep, NSMakeSize, NSMakeRect, NSString, NSFontAttributeName, NSPNGFileType, NSBezierPath
from Foundation import NSDictionary

out = pathlib.Path(sys.argv[1])
work = pathlib.Path(tempfile.mkdtemp()) / "BrainAI.iconset"
work.mkdir()

def render(px):
    img = NSImage.alloc().initWithSize_(NSMakeSize(px, px))
    img.lockFocus()
    NSColor.colorWithSRGBRed_green_blue_alpha_(0.16, 0.18, 0.24, 1.0).setFill()
    NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(NSMakeRect(0, 0, px, px), px * 0.22, px * 0.22).fill()
    s = NSString.stringWithString_("🧠")
    attrs = NSDictionary.dictionaryWithObjects_forKeys_([NSFont.systemFontOfSize_(px * 0.68)], [NSFontAttributeName])
    sz = s.sizeWithAttributes_(attrs)
    s.drawAtPoint_withAttributes_(((px - sz.width) / 2, (px - sz.height) / 2), attrs)
    img.unlockFocus()
    tiff = img.TIFFRepresentation()
    rep = NSBitmapImageRep.imageRepWithData_(tiff)
    return rep.representationUsingType_properties_(NSPNGFileType, None)

for base in (16, 32, 128, 256, 512):
    for scale in (1, 2):
        px = base * scale
        name = f"icon_{base}x{base}{'@2x' if scale == 2 else ''}.png"
        render(px).writeToFile_atomically_(str(work / name), True)

subprocess.check_call(["iconutil", "-c", "icns", str(work), "-o", str(out)])
print(out)
