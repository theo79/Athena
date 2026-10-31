"""Generate the static RGB565 screen from the original PNG; no target decoder."""
import argparse
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    with Image.open(args.source) as original:
        rgba = original.convert("RGBA")
        flattened = Image.new("RGBA", rgba.size, "white")
        flattened.alpha_composite(rgba)
        logo = ImageOps.contain(flattened.convert("RGB"), (208, 208), Image.Resampling.LANCZOS)
    screen = Image.new("RGB", (240, 280), "white")
    screen.paste(logo, ((240 - logo.width) // 2, 8 + (208 - logo.height) // 2))
    draw = ImageDraw.Draw(screen)
    # Pillow's bundled font makes this independent of host-installed fonts.
    for text, size, y in (("ATHENA", 20, 218), ("v0.1.0", 13, 242),
                          ("Hardware: ESP32-S3", 10, 259), ("Status: Ready", 10, 271)):
        draw.text((120, y), text, font=ImageFont.load_default(size=size),
                  fill=(34, 41, 49), anchor="mt")
    shutdown = Image.new("RGB", (240, 40), "white")
    ImageDraw.Draw(shutdown).text((120, 12), "Shutting down...",
                                  font=ImageFont.load_default(size=19),
                                  fill=(34, 41, 49), anchor="mt")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    chunks = ["// Generated; RGB565, most significant byte first.\n#include <stdint.h>\n"]
    for name, image in (("athena_startup_rgb565", screen), ("athena_shutdown_rgb565", shutdown)):
        data = bytearray()
        for r, g, b in image.getdata():
            pixel = ((r >> 3) << 11) | ((g >> 2) << 5) | (b >> 3)
            data.extend((pixel >> 8, pixel & 255))
        rows = [", ".join(f"0x{v:02x}" for v in data[i:i + 24]) for i in range(0, len(data), 24)]
        chunks.append(f"const uint8_t {name}[{len(data)}] = {{\n" + ",\n".join(rows) + "\n};\n")
    args.output.write_text("".join(chunks), encoding="utf-8")
    screen.save(args.output.with_suffix(".png"))
    shutdown.save(args.output.with_name("shutdown_message.png"))
    print(f"Startup: {logo.width}x{logo.height} logo; shutdown strip: 240x40; RGB565")


if __name__ == "__main__":
    main()
