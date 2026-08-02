from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CARD_PATH = Path(__file__).resolve().parents[2] / "runtime" / "limit_ads_price.png"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    candidates = [
        Path("/usr/share/fonts/truetype/dejavu") / name,
        Path("/usr/share/fonts/truetype/dejavu") / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _rounded_gradient(width: int, height: int) -> Image.Image:
    image = Image.new("RGB", (width, height))
    pixels = image.load()
    for y in range(height):
        fy = y / max(height - 1, 1)
        for x in range(width):
            fx = x / max(width - 1, 1)
            glow = max(0.0, 1.0 - ((fx - 0.52) ** 2 + (fy - 0.18) ** 2) * 3.2)
            r = int(105 + 112 * fy + 28 * glow)
            g = int(35 + 42 * (1 - fy) + 44 * glow)
            b = int(115 + 70 * (1 - fx) + 46 * glow)
            pixels[x, y] = (min(r, 255), min(g, 255), min(b, 255))
    return image


def ensure_price_card() -> Path:
    if CARD_PATH.exists():
        return CARD_PATH

    CARD_PATH.parent.mkdir(parents=True, exist_ok=True)
    width, height = 1200, 1200
    image = _rounded_gradient(width, height)

    clouds = Image.new("RGBA", image.size, (0, 0, 0, 0))
    cloud_draw = ImageDraw.Draw(clouds)
    cloud_specs = [
        (100, 120, 250, (255, 159, 194, 80)),
        (330, 65, 210, (255, 190, 174, 70)),
        (650, 120, 270, (255, 145, 190, 75)),
        (920, 70, 230, (255, 190, 175, 65)),
        (55, 875, 300, (215, 82, 190, 55)),
        (790, 875, 340, (255, 104, 173, 55)),
    ]
    for cx, cy, radius, color in cloud_specs:
        for ox, oy, scale in [(-0.28, 0.05, 0.55), (0.0, -0.12, 0.7), (0.3, 0.08, 0.55)]:
            rr = radius * scale
            cloud_draw.ellipse(
                (cx + ox * radius - rr, cy + oy * radius - rr, cx + ox * radius + rr, cy + oy * radius + rr),
                fill=color,
            )
    clouds = clouds.filter(ImageFilter.GaussianBlur(22))
    image = Image.alpha_composite(image.convert("RGBA"), clouds)

    draw = ImageDraw.Draw(image)
    shadow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.rounded_rectangle((65, 250, 1135, 1095), radius=58, fill=(20, 8, 35, 120))
    shadow = shadow.filter(ImageFilter.GaussianBlur(24))
    image = Image.alpha_composite(image, shadow)
    draw = ImageDraw.Draw(image)

    draw.rounded_rectangle(
        (65, 230, 1135, 1075),
        radius=58,
        fill=(25, 15, 38, 218),
        outline=(255, 180, 220, 135),
        width=3,
    )

    title_font = _font(128, bold=True)
    sub_font = _font(38, bold=True)
    header_font = _font(38, bold=True)
    body_font = _font(43, bold=True)
    price_font = _font(40, bold=True)
    note_font = _font(28)

    title = "Limit"
    title_box = draw.textbbox((0, 0), title, font=title_font, stroke_width=2)
    title_width = title_box[2] - title_box[0]
    title_x = (width - title_width) // 2
    draw.text((title_x + 7, 62 + 9), title, font=title_font, fill=(20, 9, 32, 180), stroke_width=5, stroke_fill=(20, 9, 32, 180))
    draw.text((title_x, 62), title, font=title_font, fill=(255, 231, 219), stroke_width=5, stroke_fill=(62, 28, 83))

    subtitle = "PRICE ADS"
    sub_box = draw.textbbox((0, 0), subtitle, font=sub_font)
    draw.text(((width - (sub_box[2] - sub_box[0])) // 2, 190), subtitle, font=sub_font, fill=(255, 169, 219))

    x_positions = [105, 425, 665, 895]
    headers = ["ТАРИФ", "ДЕНЬ", "НЕДЕЛЯ", "МЕСЯЦ"]
    for x, text in zip(x_positions, headers):
        draw.text((x, 282), text, font=header_font, fill=(255, 213, 236))
    draw.line((100, 345, 1100, 345), fill=(255, 158, 215, 170), width=3)

    rows = [
        ("STANDARD", "500 ₽", "1 000 ₽", "1 500 ₽", (241, 219, 255)),
        ("MIDDLE", "700 ₽", "1 400 ₽", "2 000 ₽", (255, 205, 235)),
        ("BEST", "1 500 ₽", "2 000 ₽", "2 700 ₽", (255, 228, 179)),
    ]
    row_y = [410, 610, 810]
    for index, ((name, day, week, month, accent), y) in enumerate(zip(rows, row_y), start=1):
        draw.rounded_rectangle(
            (95, y - 35, 1105, y + 120),
            radius=32,
            fill=(255, 255, 255, 18),
            outline=accent + (90,),
            width=2,
        )
        draw.ellipse((120, y + 2, 178, y + 60), fill=accent + (255,))
        number = str(index)
        nb = draw.textbbox((0, 0), number, font=header_font)
        draw.text((149 - (nb[2] - nb[0]) / 2, y + 2), number, font=header_font, fill=(42, 20, 57))
        draw.text((195, y - 3), name, font=body_font, fill=accent)
        draw.text((450, y + 2), day, font=price_font, fill=(255, 255, 255))
        draw.text((690, y + 2), week, font=price_font, fill=(255, 255, 255))
        draw.text((925, y + 2), month, font=price_font, fill=(255, 255, 255))

    draw.text((105, 1007), "Реклама для барахолки Limit Vape", font=note_font, fill=(232, 207, 235))
    image.convert("RGB").save(CARD_PATH, format="PNG", optimize=True)
    return CARD_PATH
