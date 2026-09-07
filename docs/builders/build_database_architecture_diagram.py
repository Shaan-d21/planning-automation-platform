from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image, ImageDraw, ImageFont


ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = ROOT / "outputs" / "architecture"
SVG_PATH = OUTPUT_DIR / "database-architecture.svg"
PNG_PATH = OUTPUT_DIR / "database-architecture.png"

WIDTH = 2600
HEIGHT = 1750

NAVY = "#0B1F3A"
BLUE = "#2F5BEA"
BLUE_LIGHT = "#EAF0FF"
TEAL = "#008A72"
TEAL_LIGHT = "#E2F6F1"
ORANGE = "#C56A00"
ORANGE_LIGHT = "#FFF1D9"
PURPLE = "#6D4FD3"
PURPLE_LIGHT = "#F0ECFF"
RED = "#C83B4A"
INK = "#17243B"
MUTED = "#60708A"
LINE = "#B8C5D8"
SURFACE = "#FFFFFF"
PAGE = "#F5F8FC"


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = [
        Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
        Path("C:/Windows/Fonts/segoeuib.ttf" if bold else "C:/Windows/Fonts/segoeui.ttf"),
    ]
    for candidate in candidates:
        if candidate.exists():
            return ImageFont.truetype(str(candidate), size=size)
    return ImageFont.load_default()


def _rounded(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], fill: str, outline: str, radius: int = 22, width: int = 2) -> None:
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def _arrow(draw: ImageDraw.ImageDraw, points: list[tuple[int, int]], color: str = LINE, width: int = 5) -> None:
    draw.line(points, fill=color, width=width, joint="curve")
    (x1, y1), (x2, y2) = points[-2], points[-1]
    if abs(x2 - x1) >= abs(y2 - y1):
        direction = 1 if x2 > x1 else -1
        head = [(x2, y2), (x2 - 14 * direction, y2 - 9), (x2 - 14 * direction, y2 + 9)]
    else:
        direction = 1 if y2 > y1 else -1
        head = [(x2, y2), (x2 - 9, y2 - 14 * direction), (x2 + 9, y2 - 14 * direction)]
    draw.polygon(head, fill=color)


def _system_box(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], title: str, subtitle: str, accent: str) -> None:
    x1, y1, x2, y2 = box
    _rounded(draw, box, SURFACE, LINE, radius=20, width=2)
    draw.rounded_rectangle((x1, y1, x1 + 13, y2), radius=8, fill=accent)
    draw.text((x1 + 34, y1 + 25), title, font=_font(27, True), fill=NAVY)
    draw.text((x1 + 34, y1 + 67), subtitle, font=_font(18), fill=MUTED)


def _domain_box(
    draw: ImageDraw.ImageDraw,
    box: tuple[int, int, int, int],
    title: str,
    subtitle: str,
    tables: list[str],
    accent: str,
    fill: str,
    columns: int = 2,
) -> None:
    x1, y1, x2, y2 = box
    _rounded(draw, box, fill, accent, radius=22, width=3)
    draw.text((x1 + 28, y1 + 23), title, font=_font(27, True), fill=NAVY)
    draw.text((x1 + 28, y1 + 63), subtitle, font=_font(17), fill=MUTED)
    draw.line((x1 + 28, y1 + 99, x2 - 28, y1 + 99), fill=accent, width=2)

    col_width = (x2 - x1 - 58) // columns
    rows = (len(tables) + columns - 1) // columns
    top = y1 + 124
    row_height = min(45, max(34, (y2 - top - 26) // max(rows, 1)))
    for index, table in enumerate(tables):
        col = index // rows
        row = index % rows
        tx = x1 + 30 + col * col_width
        ty = top + row * row_height
        draw.rounded_rectangle((tx, ty + 6, tx + 18, ty + 24), radius=4, fill=accent)
        draw.line((tx + 4, ty + 12, tx + 14, ty + 12), fill=SURFACE, width=2)
        draw.line((tx + 4, ty + 18, tx + 14, ty + 18), fill=SURFACE, width=2)
        draw.text((tx + 28, ty + 3), table, font=_font(17, False), fill=INK)


def build_png() -> None:
    image = Image.new("RGB", (WIDTH, HEIGHT), PAGE)
    draw = ImageDraw.Draw(image)

    draw.text((78, 42), "BISP EPM Automation — Database Architecture", font=_font(42, True), fill=NAVY)
    draw.text(
        (80, 101),
        "Current PostgreSQL control-plane schema · 39 application tables · Oracle EPM remains the system of record for Planning data",
        font=_font(21),
        fill=MUTED,
    )

    _system_box(draw, (80, 165, 650, 300), "Oracle EPM Cloud / On-Prem", "Planning artifacts, cube data, jobs and Inbox/Outbox", BLUE)
    _system_box(draw, (735, 165, 1255, 300), "Enterprise identity provider", "Oracle Cloud OIDC, external identities and entitlements", PURPLE)
    _system_box(draw, (1340, 165, 1860, 300), "AI model provider", "Groq or Gemini through the provider-neutral agent layer", TEAL)
    _system_box(draw, (1945, 165, 2520, 300), "Operations services", "SMTP notifications, browser clients, Excel and API callers", ORANGE)

    _rounded(draw, (80, 350, 2520, 535), SURFACE, NAVY, radius=24, width=3)
    draw.text((110, 375), "Application control plane", font=_font(28, True), fill=NAVY)
    services = [
        (120, "FastAPI web/API", "Authentication · CSRF · RBAC · validation"),
        (720, "Application services", "Planning cycles · catalog · data review"),
        (1320, "LangGraph assistant", "Intent · tools · review · approval"),
        (1920, "Workers and scheduler", "Durable claims · leases · monitoring"),
    ]
    for x, heading, body in services:
        _rounded(draw, (x, 430, x + 500, 510), PAGE, LINE, radius=14, width=2)
        draw.text((x + 18, 445), heading, font=_font(21, True), fill=INK)
        draw.text((x + 18, 478), body, font=_font(15), fill=MUTED)

    # External-to-control-plane paths.
    _arrow(draw, [(365, 305), (365, 340), (360, 340), (360, 427)], BLUE)
    _arrow(draw, [(995, 305), (995, 340), (370, 340), (370, 427)], PURPLE)
    _arrow(draw, [(1600, 305), (1600, 340), (1570, 340), (1570, 427)], TEAL)
    _arrow(draw, [(2230, 305), (2230, 340), (2170, 340), (2170, 427)], ORANGE)

    # PostgreSQL boundary.
    _rounded(draw, (55, 590, 2545, 1635), "#EEF3FA", NAVY, radius=28, width=4)
    draw.text((92, 615), "PostgreSQL", font=_font(32, True), fill=NAVY)
    draw.text((300, 622), "Application schema managed by Alembic (current head: 0020_execution_identity)", font=_font(19), fill=MUTED)

    identity_tables = [
        "platform_users", "platform_roles", "platform_permissions*", "platform_role_permissions",
        "platform_user_roles", "authentication_events", "identity_providers", "external_identities",
        "external_entitlements", "external_identity_entitlements", "identity_role_mappings",
        "identity_sync_runs", "api_tokens", "oracle_environment_settings",
    ]
    # platform_permissions is represented by the permission catalog in code rather than a table.
    identity_tables.remove("platform_permissions*")
    catalog_tables = [
        "oracle_artifacts", "business_rule_rtp_sync_runs",
        "business_rule_rtp_definitions", "business_rule_rtp_parameters",
    ]
    agent_tables = [
        "agent_conversations", "agent_messages", "agent_tool_activities",
        "agent_action_drafts", "agent_action_decisions",
    ]
    execution_tables = [
        "workflow_runs", "workflow_steps", "execution_queue", "automation_schedules",
        "automation_schedule_runs", "process_schedules",
    ]
    lifecycle_tables = [
        "planning_cycles", "planning_cycle_stages", "planning_tasks", "planning_task_dependencies",
        "planning_task_executions", "planning_task_validations", "planning_approvals", "user_notifications",
    ]
    retained_tables = ["planning_processes", "planning_process_versions", "process_run_profiles"]

    draw.text((830, 674), "Governed references", font=_font(17, True), fill=INK)
    draw.text((1015, 674), "users · roles · environment · artifacts · execution IDs", font=_font(17), fill=MUTED)
    draw.line((335, 695, 2260, 695), fill=LINE, width=4)

    _domain_box(draw, (90, 720, 805, 1125), "Identity & access", "Users, roles, sessions, federation and tokens", identity_tables, BLUE, BLUE_LIGHT, columns=2)
    _domain_box(draw, (835, 720, 1360, 1125), "Oracle catalog & RTP", "Environment-scoped discovery and prompt metadata", catalog_tables, ORANGE, ORANGE_LIGHT, columns=1)
    _domain_box(draw, (1390, 720, 1915, 1125), "EPM Assistant", "Conversation history and governed decisions", agent_tables, PURPLE, PURPLE_LIGHT, columns=1)
    _domain_box(draw, (1945, 720, 2510, 1125), "Execution & scheduling", "Durable work, steps, leases and occurrences", execution_tables, TEAL, TEAL_LIGHT, columns=1)
    _domain_box(draw, (90, 1160, 1125, 1525), "Planning lifecycle", "Cycle-to-task operating model and review evidence", lifecycle_tables, BLUE, SURFACE, columns=2)
    _domain_box(draw, (1155, 1160, 1745, 1525), "Retained process definitions", "Compatibility structures; not the current user-facing designer", retained_tables, ORANGE, SURFACE, columns=1)
    _domain_box(draw, (1775, 1160, 2510, 1525), "LangGraph checkpoints", "Framework-owned state, managed outside Alembic", ["checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations"], NAVY, SURFACE, columns=1)

    # Clean relationship bus: dependencies are explicit without crossing table labels.
    for x, color in [(335, BLUE), (1095, ORANGE), (1650, PURPLE), (2260, TEAL)]:
        _arrow(draw, [(x, 695), (x, 712)], color, 4)
    _arrow(draw, [(515, 1125), (515, 1152)], BLUE, 4)
    _arrow(draw, [(1125, 1335), (1147, 1335)], ORANGE, 4)
    _arrow(draw, [(1800, 1125), (1800, 1142), (2000, 1142), (2000, 1152)], PURPLE, 4)

    # Control-plane-to-database ownership paths.
    _arrow(draw, [(360, 515), (360, 687)], BLUE)
    _arrow(draw, [(970, 515), (970, 687)], ORANGE)
    _arrow(draw, [(1570, 515), (1570, 687)], PURPLE)
    _arrow(draw, [(2170, 515), (2170, 687)], TEAL)

    draw.text((100, 1580), "Key relationship", font=_font(17, True), fill=INK)
    draw.line((275, 1592, 365, 1592), fill=LINE, width=5)
    draw.polygon([(365, 1592), (350, 1583), (350, 1601)], fill=LINE)
    draw.text((385, 1580), "ownership, foreign-key dependency, or governed hand-off", font=_font(17), fill=MUTED)
    draw.text((1390, 1580), "Boundary rule", font=_font(17, True), fill=INK)
    draw.text((1515, 1580), "No Planning cube values, Oracle passwords, or job configuration are copied into PostgreSQL.", font=_font(17), fill=MUTED)

    image.save(PNG_PATH, optimize=True)


def _svg_text(x: int, y: int, value: str, size: int, color: str, weight: int = 400) -> str:
    return f'<text x="{x}" y="{y}" font-family="Arial, Segoe UI, sans-serif" font-size="{size}" font-weight="{weight}" fill="{color}">{escape(value)}</text>'


def build_svg() -> None:
    # The SVG mirrors the PNG layout and remains editable in design tools.
    parts: list[str] = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{WIDTH}" height="{HEIGHT}" viewBox="0 0 {WIDTH} {HEIGHT}">',
        f'<rect width="{WIDTH}" height="{HEIGHT}" fill="{PAGE}"/>',
        '<defs><marker id="arrow" markerWidth="10" markerHeight="10" refX="8" refY="3" orient="auto" markerUnits="strokeWidth"><path d="M0,0 L0,6 L9,3 z" fill="#B8C5D8"/></marker></defs>',
        _svg_text(78, 78, "BISP EPM Automation — Database Architecture", 42, NAVY, 700),
        _svg_text(80, 126, "Current PostgreSQL control-plane schema · 39 application tables · Oracle EPM remains the system of record for Planning data", 21, MUTED),
    ]

    def rect(box: tuple[int, int, int, int], fill: str, stroke: str, radius: int = 20, width: int = 2) -> None:
        x1, y1, x2, y2 = box
        parts.append(f'<rect x="{x1}" y="{y1}" width="{x2-x1}" height="{y2-y1}" rx="{radius}" fill="{fill}" stroke="{stroke}" stroke-width="{width}"/>')

    def system(box: tuple[int, int, int, int], title: str, subtitle: str, accent: str) -> None:
        x1, y1, x2, y2 = box
        rect(box, SURFACE, LINE)
        parts.append(f'<rect x="{x1}" y="{y1}" width="13" height="{y2-y1}" rx="7" fill="{accent}"/>')
        parts.append(_svg_text(x1 + 34, y1 + 52, title, 27, NAVY, 700))
        parts.append(_svg_text(x1 + 34, y1 + 92, subtitle, 18, MUTED))

    system((80, 165, 650, 300), "Oracle EPM Cloud / On-Prem", "Planning artifacts, cube data, jobs and Inbox/Outbox", BLUE)
    system((735, 165, 1255, 300), "Enterprise identity provider", "Oracle Cloud OIDC, external identities and entitlements", PURPLE)
    system((1340, 165, 1860, 300), "AI model provider", "Groq or Gemini through the provider-neutral agent layer", TEAL)
    system((1945, 165, 2520, 300), "Operations services", "SMTP notifications, browser clients, Excel and API callers", ORANGE)

    rect((80, 350, 2520, 535), SURFACE, NAVY, 24, 3)
    parts.append(_svg_text(110, 410, "Application control plane", 28, NAVY, 700))
    services = [
        (120, "FastAPI web/API", "Authentication · CSRF · RBAC · validation"),
        (720, "Application services", "Planning cycles · catalog · data review"),
        (1320, "LangGraph assistant", "Intent · tools · review · approval"),
        (1920, "Workers and scheduler", "Durable claims · leases · monitoring"),
    ]
    for x, heading, body in services:
        rect((x, 430, x + 500, 510), PAGE, LINE, 14, 2)
        parts.append(_svg_text(x + 18, 468, heading, 21, INK, 700))
        parts.append(_svg_text(x + 18, 496, body, 15, MUTED))

    for path, color in [
        ("M365 305 V340 H360 V427", BLUE),
        ("M995 305 V340 H370 V427", PURPLE),
        ("M1600 305 V340 H1570 V427", TEAL),
        ("M2230 305 V340 H2170 V427", ORANGE),
    ]:
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="5" marker-end="url(#arrow)"/>')

    rect((55, 590, 2545, 1635), "#EEF3FA", NAVY, 28, 4)
    parts.append(_svg_text(92, 655, "PostgreSQL", 32, NAVY, 700))
    parts.append(_svg_text(300, 650, "Application schema managed by Alembic (current head: 0020_execution_identity)", 19, MUTED))

    def domain(box: tuple[int, int, int, int], title: str, subtitle: str, tables: list[str], accent: str, fill: str, columns: int = 2) -> None:
        x1, y1, x2, y2 = box
        rect(box, fill, accent, 22, 3)
        parts.append(_svg_text(x1 + 28, y1 + 52, title, 27, NAVY, 700))
        parts.append(_svg_text(x1 + 28, y1 + 83, subtitle, 17, MUTED))
        parts.append(f'<line x1="{x1+28}" y1="{y1+99}" x2="{x2-28}" y2="{y1+99}" stroke="{accent}" stroke-width="2"/>')
        col_width = (x2 - x1 - 58) // columns
        rows = (len(tables) + columns - 1) // columns
        top = y1 + 124
        row_height = min(45, max(34, (y2 - top - 26) // max(rows, 1)))
        for index, table in enumerate(tables):
            col = index // rows
            row = index % rows
            tx = x1 + 30 + col * col_width
            ty = top + row * row_height
            parts.append(f'<rect x="{tx}" y="{ty+6}" width="18" height="18" rx="4" fill="{accent}"/>')
            parts.append(f'<line x1="{tx+4}" y1="{ty+12}" x2="{tx+14}" y2="{ty+12}" stroke="{SURFACE}" stroke-width="2"/>')
            parts.append(f'<line x1="{tx+4}" y1="{ty+18}" x2="{tx+14}" y2="{ty+18}" stroke="{SURFACE}" stroke-width="2"/>')
            parts.append(_svg_text(tx + 28, ty + 22, table, 17, INK))

    parts.append(_svg_text(830, 680, "Governed references", 17, INK, 700))
    parts.append(_svg_text(1015, 680, "users · roles · environment · artifacts · execution IDs", 17, MUTED))
    parts.append('<line x1="335" y1="695" x2="2260" y2="695" stroke="#B8C5D8" stroke-width="4"/>')

    domain((90, 720, 805, 1125), "Identity & access", "Users, roles, sessions, federation and tokens", [
        "platform_users", "platform_roles", "platform_role_permissions", "platform_user_roles",
        "authentication_events", "identity_providers", "external_identities", "external_entitlements",
        "external_identity_entitlements", "identity_role_mappings", "identity_sync_runs", "api_tokens",
        "oracle_environment_settings",
    ], BLUE, BLUE_LIGHT, 2)
    domain((835, 720, 1360, 1125), "Oracle catalog & RTP", "Environment-scoped discovery and prompt metadata", [
        "oracle_artifacts", "business_rule_rtp_sync_runs", "business_rule_rtp_definitions", "business_rule_rtp_parameters",
    ], ORANGE, ORANGE_LIGHT, 1)
    domain((1390, 720, 1915, 1125), "EPM Assistant", "Conversation history and governed decisions", [
        "agent_conversations", "agent_messages", "agent_tool_activities", "agent_action_drafts", "agent_action_decisions",
    ], PURPLE, PURPLE_LIGHT, 1)
    domain((1945, 720, 2510, 1125), "Execution & scheduling", "Durable work, steps, leases and occurrences", [
        "workflow_runs", "workflow_steps", "execution_queue", "automation_schedules", "automation_schedule_runs", "process_schedules",
    ], TEAL, TEAL_LIGHT, 1)
    domain((90, 1160, 1125, 1525), "Planning lifecycle", "Cycle-to-task operating model and review evidence", [
        "planning_cycles", "planning_cycle_stages", "planning_tasks", "planning_task_dependencies",
        "planning_task_executions", "planning_task_validations", "planning_approvals", "user_notifications",
    ], BLUE, SURFACE, 2)
    domain((1155, 1160, 1745, 1525), "Retained process definitions", "Compatibility structures; not the current user-facing designer", [
        "planning_processes", "planning_process_versions", "process_run_profiles",
    ], ORANGE, SURFACE, 1)
    domain((1775, 1160, 2510, 1525), "LangGraph checkpoints", "Framework-owned state, managed outside Alembic", [
        "checkpoints", "checkpoint_blobs", "checkpoint_writes", "checkpoint_migrations",
    ], NAVY, SURFACE, 1)

    for path, color in [
        ("M335 695 V712", BLUE), ("M1095 695 V712", ORANGE), ("M1650 695 V712", PURPLE), ("M2260 695 V712", TEAL),
        ("M515 1125 V1152", BLUE), ("M1125 1335 H1147", ORANGE), ("M1800 1125 V1142 H2000 V1152", PURPLE),
        ("M360 515 V687", BLUE), ("M970 515 V687", ORANGE), ("M1570 515 V687", PURPLE), ("M2170 515 V687", TEAL),
    ]:
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="4" marker-end="url(#arrow)"/>')

    parts.extend([
        _svg_text(100, 1600, "Key relationship", 17, INK, 700),
        '<line x1="275" y1="1592" x2="365" y2="1592" stroke="#B8C5D8" stroke-width="5" marker-end="url(#arrow)"/>',
        _svg_text(385, 1600, "ownership, foreign-key dependency, or governed hand-off", 17, MUTED),
        _svg_text(1390, 1600, "Boundary rule", 17, INK, 700),
        _svg_text(1515, 1600, "No Planning cube values, Oracle passwords, or job configuration are copied into PostgreSQL.", 17, MUTED),
        '</svg>',
    ])
    SVG_PATH.write_text("\n".join(parts), encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    build_svg()
    build_png()
    print(SVG_PATH)
    print(PNG_PATH)


if __name__ == "__main__":
    main()
