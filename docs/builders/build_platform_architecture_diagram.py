from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "outputs" / "architecture"
PNG_PATH = OUTPUT_DIR / "platform-architecture.png"
SVG_PATH = OUTPUT_DIR / "platform-architecture.svg"

W, H = 2600, 1800

PAGE = "#F5F8FC"
WHITE = "#FFFFFF"
NAVY = "#0B1F3A"
INK = "#17243B"
MUTED = "#60708A"
LINE = "#B8C5D8"
BLUE = "#2F5BEA"
BLUE_LIGHT = "#EAF0FF"
TEAL = "#008A72"
TEAL_LIGHT = "#E2F6F1"
PURPLE = "#6D4FD3"
PURPLE_LIGHT = "#F0ECFF"
ORANGE = "#C56A00"
ORANGE_LIGHT = "#FFF1D9"


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


@dataclass
class TextOp:
    x: int
    y: int
    text: str
    size: int
    color: str
    bold: bool = False


class Scene:
    def __init__(self) -> None:
        self.image = Image.new("RGB", (W, H), PAGE)
        self.draw = ImageDraw.Draw(self.image)
        self.svg: list[str] = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">',
            f'<rect width="{W}" height="{H}" fill="{PAGE}"/>',
            '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="context-stroke"/></marker></defs>',
        ]

    def text(self, x: int, y: int, value: str, size: int, color: str = INK, bold: bool = False) -> None:
        self.draw.text((x, y), value, font=font(size, bold), fill=color)
        self.svg.append(
            f'<text x="{x}" y="{y + size}" font-family="Arial, Segoe UI, sans-serif" '
            f'font-size="{size}" font-weight="{700 if bold else 400}" fill="{color}">{escape(value)}</text>'
        )

    def rounded(self, box: tuple[int, int, int, int], fill: str, stroke: str, width: int = 2, radius: int = 20) -> None:
        x1, y1, x2, y2 = box
        self.draw.rounded_rectangle(box, radius=radius, fill=fill, outline=stroke, width=width)
        self.svg.append(
            f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="{radius}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>'
        )

    def line(self, points: list[tuple[int, int]], color: str = LINE, width: int = 4, arrow: bool = False) -> None:
        self.draw.line(points, fill=color, width=width, joint="curve")
        coords = " ".join(f"{x},{y}" for x, y in points)
        marker = ' marker-end="url(#arrow)"' if arrow else ""
        self.svg.append(f'<polyline points="{coords}" fill="none" stroke="{color}" stroke-width="{width}" stroke-linejoin="round"{marker}/>')
        if arrow:
            (x1, y1), (x2, y2) = points[-2], points[-1]
            if abs(x2 - x1) >= abs(y2 - y1):
                direction = 1 if x2 > x1 else -1
                head = [(x2, y2), (x2 - 14 * direction, y2 - 9), (x2 - 14 * direction, y2 + 9)]
            else:
                direction = 1 if y2 > y1 else -1
                head = [(x2, y2), (x2 - 9, y2 - 14 * direction), (x2 + 9, y2 - 14 * direction)]
            self.draw.polygon(head, fill=color)

    def box(self, box: tuple[int, int, int, int], title: str, subtitle: str, accent: str, fill: str = WHITE, label: str | None = None) -> None:
        x1, y1, x2, y2 = box
        self.rounded(box, fill, accent, width=3, radius=20)
        self.draw.rounded_rectangle((x1, y1, x1 + 12, y2), radius=7, fill=accent)
        self.svg.append(f'<rect x="{x1}" y="{y1}" width="12" height="{y2-y1}" rx="7" fill="{accent}"/>')
        if label:
            self.text(x1 + 30, y1 + 17, label.upper(), 14, accent, True)
            title_y = y1 + 43
            sub_y = y1 + 79
        else:
            title_y = y1 + 23
            sub_y = y1 + 62
        self.text(x1 + 30, title_y, title, 24, NAVY, True)
        self.text(x1 + 30, sub_y, subtitle, 16, MUTED)

    def save(self) -> None:
        self.svg.append("</svg>")
        self.image.save(PNG_PATH, optimize=True)
        SVG_PATH.write_text("\n".join(self.svg), encoding="utf-8")


def build() -> None:
    s = Scene()
    s.text(72, 38, "BISP EPM Automation — Platform Architecture", 42, NAVY, True)
    s.text(74, 96, "Current production-oriented design · one governed platform for Planning work, automation and AI-assisted operations", 21, MUTED)

    # Entry channels.
    s.box((70, 165, 610, 300), "People", "User · Power User · Service Administrator · Viewer", BLUE, WHITE, "Actors")
    s.box((670, 165, 1190, 300), "React web experience", "Role-aware workspace, Smart Grid and guided operation runners", BLUE, BLUE_LIGHT, "Primary channel")
    s.box((1250, 165, 1770, 300), "Excel, API and CLI", "Scoped tokens and shared versioned API contracts", TEAL, WHITE, "Other channels")
    s.box((1830, 165, 2530, 300), "Approved automation schedules", "One-time or recurring Pipeline and RTP catalog synchronization", ORANGE, ORANGE_LIGHT, "Unattended channel")

    # Main platform boundary.
    s.rounded((45, 350, 2555, 1505), "#EEF3FA", NAVY, width=4, radius=28)
    s.text(82, 375, "BISP EPM Automation platform", 31, NAVY, True)
    s.text(720, 383, "Modular application core shared by every supported channel", 18, MUTED)

    # Connect entry channels to delivery layer through clean lanes.
    s.line([(610, 232), (660, 232)], BLUE, 5, True)
    s.line([(930, 300), (930, 330), (340, 330), (340, 470)], BLUE, 5, True)
    s.line([(1510, 300), (1510, 330), (520, 330), (520, 690)], TEAL, 5, True)
    s.line([(2180, 300), (2180, 330), (2535, 330), (2535, 930), (2320, 930), (2320, 945)], ORANGE, 5, True)

    # Experience layer.
    s.text(85, 440, "01  EXPERIENCE", 17, BLUE, True)
    s.box((90, 480, 800, 625), "React + TypeScript SPA", "Vite build · responsive workspaces · mouse-first inputs", BLUE, BLUE_LIGHT)
    s.box((835, 480, 1560, 625), "Planning workspaces", "My Work · cycles · Data Review · reports · Jobs & Activity", BLUE, WHITE)
    s.box((1595, 480, 2510, 625), "Governed EPM Assistant", "Natural-language discovery, recommendations, input capture and approval", PURPLE, PURPLE_LIGHT)

    # Experience to delivery connectors in the inter-layer gap.
    s.line([(445, 625), (445, 670), (380, 670), (380, 690)], BLUE, 4, True)
    s.line([(1195, 625), (1195, 670), (1040, 670), (1040, 690)], BLUE, 4, True)
    s.line([(2050, 625), (2050, 670), (1690, 670), (1690, 690)], PURPLE, 4, True)

    # Delivery and security layer.
    s.text(85, 654, "02  DELIVERY & SECURITY", 17, NAVY, True)
    s.box((90, 700, 650, 875), "FastAPI /api/v1", "Typed HTTP contracts, request IDs and secure headers", NAVY, WHITE)
    s.box((680, 700, 1240, 875), "Identity and sessions", "Local sign-in · Oracle validation · Oracle Cloud OIDC · API tokens", PURPLE, WHITE)
    s.box((1270, 700, 1830, 875), "Server-side governance", "Four-role RBAC · CSRF · validation · environment scope", BLUE, BLUE_LIGHT)
    s.box((1860, 700, 2510, 875), "File and response boundary", "Session-owned uploads · safe downloads · 100 MB upload limit", ORANGE, WHITE)

    # Delivery bus to application services.
    s.line([(370, 875), (370, 915), (2240, 915)], LINE, 4)
    for x, color in [(330, BLUE), (820, TEAL), (1310, BLUE), (1800, PURPLE), (2290, ORANGE)]:
        s.line([(x, 915), (x, 945)], color, 4, True)

    # Application layer.
    s.text(85, 910, "03  APPLICATION CORE", 17, TEAL, True)
    s.box((90, 955, 570, 1135), "Planning lifecycle", "Cycles, stages, tasks, dependencies, validations and approvals", BLUE, WHITE)
    s.box((600, 955, 1080, 1135), "Oracle catalog & runners", "Pipelines, integrations, rules, Data Maps, imports and refresh", TEAL, TEAL_LIGHT)
    s.box((1110, 955, 1590, 1135), "Data Review & reporting", "Live cube grids, reconciliation and Excel artifacts", BLUE, BLUE_LIGHT)
    s.box((1620, 955, 2100, 1135), "LangGraph agent core", "Intent, live discovery, tool calls, interrupts and decisions", PURPLE, PURPLE_LIGHT)
    s.box((2130, 955, 2510, 1135), "Scheduling", "Allowlisted targets, context strategies and occurrences", ORANGE, ORANGE_LIGHT)

    # Application services to durable runtime bus.
    s.line([(330, 1135), (330, 1175), (2290, 1175)], LINE, 4)
    for x, color in [(390, BLUE), (1035, TEAL), (1685, ORANGE), (2250, NAVY)]:
        s.line([(x, 1175), (x, 1205)], color, 4, True)

    # Runtime and infrastructure layer.
    s.text(85, 1170, "04  DURABLE RUNTIME & INFRASTRUCTURE", 17, ORANGE, True)
    s.box((90, 1215, 690, 1435), "PostgreSQL control plane", "App state and queue · Alembic schema · LangGraph checkpoints", BLUE, BLUE_LIGHT)
    s.box((720, 1215, 1350, 1435), "Durable queue and workers", "Transactional claims · leases · heartbeats · monitored Oracle execution", TEAL, TEAL_LIGHT)
    s.box((1380, 1215, 1990, 1435), "Oracle integration adapters", "Planning REST v3 · Interop files · optional EPM Automate boundary", ORANGE, ORANGE_LIGHT)
    s.box((2020, 1215, 2510, 1435), "Operational services", "Runtime file staging · logs · notifications · cleanup", NAVY, WHITE)

    # Runtime internal relationships.
    s.line([(690, 1325), (708, 1325)], BLUE, 5, True)
    s.line([(1350, 1325), (1368, 1325)], TEAL, 5, True)
    s.line([(1990, 1325), (2008, 1325)], ORANGE, 5, True)

    # External systems and providers.
    s.text(72, 1535, "EXTERNAL SYSTEMS & PROVIDERS", 17, NAVY, True)
    s.box((70, 1580, 1040, 1740), "Oracle EPM Planning — authoritative system", "Applications · cubes · dimensions · forms · rules · Data Maps · integrations · Pipelines · jobs · files", ORANGE, WHITE)
    s.box((1100, 1580, 1570, 1740), "AI model providers", "Groq or Gemini; reasoning only, never direct Oracle execution", PURPLE, WHITE)
    s.box((1630, 1580, 2030, 1740), "Enterprise identity", "Oracle Cloud OIDC and mapped entitlements", BLUE, WHITE)
    s.box((2090, 1580, 2530, 1740), "Notification provider", "SMTP delivery; database evidence remains authoritative", TEAL, WHITE)

    # Outbound paths. They terminate at the external-system cards.
    s.line([(1685, 1435), (1685, 1535), (555, 1535), (555, 1570)], ORANGE, 5, True)
    s.line([(2005, 1135), (2005, 1515), (1335, 1515), (1335, 1570)], PURPLE, 5, True)
    s.line([(960, 875), (960, 900), (2570, 900), (2570, 1545), (1830, 1545), (1830, 1570)], BLUE, 4, True)
    s.line([(2250, 1435), (2250, 1570)], TEAL, 5, True)

    # Footer legend.
    s.line([(95, 1770), (165, 1770)], TEAL, 5, True)
    s.text(185, 1756, "durable governed execution", 16, MUTED)
    s.line([(500, 1770), (570, 1770)], PURPLE, 5, True)
    s.text(590, 1756, "AI reasoning and resumable state", 16, MUTED)
    s.text(1280, 1756, "Boundary rule", 16, INK, True)
    s.text(1405, 1756, "The platform coordinates and governs Oracle EPM; it does not replace Oracle's calculation or security engines.", 16, MUTED)

    s.save()


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build()
    print(SVG_PATH)
    print(PNG_PATH)


if __name__ == "__main__":
    main()
