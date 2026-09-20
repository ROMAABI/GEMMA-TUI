from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Theme:
    id: str
    name: str
    bg_app: str
    bg_sidebar: str
    bg_card: str
    bg_user_card: str
    bg_code: str
    border: str
    border_accent: str
    accent_primary: str
    accent_secondary: str
    accent_btn: str
    accent_btn_hover: str
    accent_btn_text: str
    text_primary: str
    text_secondary: str
    text_muted: str
    user_badge_bg: str
    user_badge_text: str
    user_name: str
    assistant_name: str
    bg_new_btn: str
    syntax_theme: str
    bg_popup: str = ""
    bg_hover: str = ""


THEMES: dict[str, Theme] = {
    "system": Theme(
        id="system",
        name="System (Terminal)",
        bg_app="transparent",
        bg_sidebar="transparent",
        bg_card="transparent",
        bg_user_card="rgba(255, 255, 255, 0.04)",
        bg_code="rgba(0, 0, 0, 0.25)",
        border="ansi_bright_black",
        border_accent="ansi_cyan",
        accent_primary="ansi_cyan",
        accent_secondary="ansi_blue",
        accent_btn="ansi_cyan",
        accent_btn_hover="ansi_bright_cyan",
        accent_btn_text="ansi_black",
        text_primary="ansi_default",
        text_secondary="ansi_white",
        text_muted="ansi_bright_black",
        user_badge_bg="ansi_blue",
        user_badge_text="ansi_bright_white",
        user_name="ansi_bright_blue",
        assistant_name="ansi_bright_cyan",
        bg_new_btn="transparent",
        syntax_theme="ansi_dark",
        bg_popup="ansi_black",
        bg_hover="ansi_bright_black",
    ),
    "tokyo-night": Theme(
        id="tokyo-night",
        name="Tokyo Night",
        bg_app="#0A101D",
        bg_sidebar="#0D1424",
        bg_card="#111A2E",
        bg_user_card="#162038",
        bg_code="#0B111F",
        border="#1E2B45",
        border_accent="#38BDF8",
        accent_primary="#38BDF8",
        accent_secondary="#60A5FA",
        accent_btn="#0284C7",
        accent_btn_hover="#38BDF8",
        accent_btn_text="#FFFFFF",
        text_primary="#F1F5F9",
        text_secondary="#94A3B8",
        text_muted="#64748B",
        user_badge_bg="#0369A1",
        user_badge_text="#FFFFFF",
        user_name="#60A5FA",
        assistant_name="#38BDF8",
        bg_new_btn="#11233E",
        syntax_theme="monokai",
    ),
    "deep-purple": Theme(
        id="deep-purple",
        name="Deep Purple",
        bg_app="#0A0E17",
        bg_sidebar="#0D111A",
        bg_card="#111625",
        bg_user_card="#141A2D",
        bg_code="#0B0F19",
        border="#1A2234",
        border_accent="#7C3AED",
        accent_primary="#C084FC",
        accent_secondary="#A855F7",
        accent_btn="#7C3AED",
        accent_btn_hover="#9333EA",
        accent_btn_text="#FFFFFF",
        text_primary="#E2E8F0",
        text_secondary="#94A3B8",
        text_muted="#64748B",
        user_badge_bg="#5B21B6",
        user_badge_text="#FFFFFF",
        user_name="#A78BFA",
        assistant_name="#C084FC",
        bg_new_btn="#1C1833",
        syntax_theme="monokai",
    ),
    "dracula": Theme(
        id="dracula",
        name="Dracula",
        bg_app="#282A36",
        bg_sidebar="#21222C",
        bg_card="#343746",
        bg_user_card="#3E4154",
        bg_code="#1E1F29",
        border="#44475A",
        border_accent="#BD93F9",
        accent_primary="#BD93F9",
        accent_secondary="#FF79C6",
        accent_btn="#BD93F9",
        accent_btn_hover="#FF79C6",
        accent_btn_text="#282A36",
        text_primary="#F8F8F2",
        text_secondary="#BFBFBF",
        text_muted="#6272A4",
        user_badge_bg="#6272A4",
        user_badge_text="#F8F8F2",
        user_name="#FF79C6",
        assistant_name="#BD93F9",
        bg_new_btn="#343746",
        syntax_theme="dracula",
    ),
    "nord": Theme(
        id="nord",
        name="Nord Arctic",
        bg_app="#242933",
        bg_sidebar="#1E222A",
        bg_card="#2E3440",
        bg_user_card="#353C4A",
        bg_code="#191D24",
        border="#3B4252",
        border_accent="#88C0D0",
        accent_primary="#88C0D0",
        accent_secondary="#81A1C1",
        accent_btn="#5E81AC",
        accent_btn_hover="#81A1C1",
        accent_btn_text="#ECEFF4",
        text_primary="#ECEFF4",
        text_secondary="#D8DEE9",
        text_muted="#4C566A",
        user_badge_bg="#434C5E",
        user_badge_text="#88C0D0",
        user_name="#88C0D0",
        assistant_name="#81A1C1",
        bg_new_btn="#2E3440",
        syntax_theme="nord",
    ),
    "cyberpunk": Theme(
        id="cyberpunk",
        name="Cyberpunk",
        bg_app="#0A0A12",
        bg_sidebar="#0E0E1A",
        bg_card="#141424",
        bg_user_card="#1A1A30",
        bg_code="#07070D",
        border="#2A2A48",
        border_accent="#FACC15",
        accent_primary="#FACC15",
        accent_secondary="#EC4899",
        accent_btn="#EC4899",
        accent_btn_hover="#F43F5E",
        accent_btn_text="#000000",
        text_primary="#F8FAFC",
        text_secondary="#94A3B8",
        text_muted="#64748B",
        user_badge_bg="#312E81",
        user_badge_text="#FACC15",
        user_name="#FACC15",
        assistant_name="#EC4899",
        bg_new_btn="#1A1528",
        syntax_theme="monokai",
    ),
    "emerald": Theme(
        id="emerald",
        name="Emerald Matrix",
        bg_app="#061410",
        bg_sidebar="#0A1C17",
        bg_card="#0E2922",
        bg_user_card="#13382E",
        bg_code="#040D0B",
        border="#1B4338",
        border_accent="#10B981",
        accent_primary="#10B981",
        accent_secondary="#34D399",
        accent_btn="#059669",
        accent_btn_hover="#10B981",
        accent_btn_text="#FFFFFF",
        text_primary="#ECFDF5",
        text_secondary="#6EE7B7",
        text_muted="#047857",
        user_badge_bg="#064E3B",
        user_badge_text="#34D399",
        user_name="#34D399",
        assistant_name="#10B981",
        bg_new_btn="#0B2720",
        syntax_theme="monokai",
    ),
}

DEFAULT_THEME = "tokyo-night"


def get_theme(theme_id: str) -> Theme:
    return THEMES.get(theme_id, THEMES[DEFAULT_THEME])


def generate_theme_css(theme: Theme) -> str:
    """Generate scoped CSS rules for a given theme."""
    p = f".theme-{theme.id}"
    popup_bg = theme.bg_popup if theme.bg_popup else theme.bg_card
    hover_bg = theme.bg_hover if theme.bg_hover else theme.bg_card
    user_hover_bg = theme.bg_hover if theme.bg_hover else theme.bg_user_card
    active_session_bg = "rgba(255, 255, 255, 0.06)" if theme.id == "system" else theme.bg_user_card
    item_hover_text = "ansi_bright_white" if theme.id == "system" else theme.text_primary
    return f"""
    Screen{p} {{
        background: {theme.bg_app};
        color: {theme.text_primary};
    }}

    {p} #sidebar {{
        background: {theme.bg_sidebar};
        border-right: solid {theme.border};
    }}

    {p} #brand {{
        color: {theme.accent_primary};
    }}

    {p} #brand-sub {{
        color: {theme.text_muted};
    }}

    {p} .nav-section-title {{
        color: {theme.text_muted};
    }}

    {p} #nav-new {{
        background: {theme.bg_new_btn};
        border: solid {theme.border_accent};
        color: {theme.text_primary};
    }}

    {p} #nav-new:hover {{
        background: {theme.accent_btn};
        color: {theme.accent_btn_text};
    }}

    {p} #nav-chats {{
        color: {theme.text_secondary};
    }}

    {p} #nav-chats:hover {{
        background: {hover_bg};
        color: {theme.text_primary};
    }}

    {p} #sessions > ListItem {{
        color: {theme.text_secondary};
    }}

    {p} #sessions > ListItem:hover,
    {p} #sessions > ListItem.--highlight {{
        background: {hover_bg};
        color: {item_hover_text};
    }}

    {p} #sessions > ListItem.active-session {{
        background: {active_session_bg};
        border-left: solid {theme.accent_primary};
        color: {theme.accent_primary};
    }}

    {p} #sessions > ListItem.active-session .session-title {{
        color: {theme.accent_primary};
        text-style: bold;
    }}

    {p} #sessions > ListItem.active-session .session-time {{
        color: {theme.accent_secondary};
    }}

    {p} .session-time {{
        color: {theme.text_muted};
    }}

    {p} #nav-settings {{
        color: {theme.text_secondary};
        border-top: solid {theme.border};
    }}

    {p} #nav-settings:hover {{
        background: {theme.bg_card};
        color: {theme.accent_primary};
    }}

    {p} #workspace {{
        background: {theme.bg_app};
    }}

    {p} #topbar {{
        background: {theme.bg_app};
        border-bottom: solid {theme.border};
    }}

    {p} #topbar-model {{
        color: {theme.text_primary};
    }}

    {p} #topbar-model:hover {{
        color: {theme.accent_primary};
    }}

    {p} .topbar-divider {{
        color: {theme.border};
    }}

    {p} #topbar-time {{
        color: {theme.text_secondary};
    }}

    {p} #topbar-help {{
        color: {theme.text_muted};
    }}

    {p} #topbar-help:hover {{
        color: {theme.accent_primary};
    }}

    {p} #nav-toggle {{
        color: {theme.text_secondary};
    }}

    {p} #nav-toggle:hover {{
        color: {theme.accent_primary};
    }}

    {p} #landing {{
        background: {theme.bg_app};
    }}

    {p} #landing-brand-icon {{
        color: {theme.accent_primary};
    }}

    {p} #landing-brand-title {{
        color: {theme.text_primary};
    }}

    {p} #landing-sub {{
        color: {theme.text_secondary};
    }}

    {p} .sug-card {{
        background: {theme.bg_card};
        border: solid {theme.border};
    }}

    {p} .sug-card:hover {{
        border: solid {theme.border_accent};
        background: {user_hover_bg};
    }}

    {p} .sug-num {{
        background: {theme.user_badge_bg};
        color: {theme.user_badge_text};
    }}

    {p} .sug-text {{
        color: {theme.text_primary};
    }}

    {p} #prompt-card {{
        background: {theme.bg_card};
        border: solid {theme.border_accent};
    }}

    {p} #prompt {{
        color: {theme.text_primary};
    }}

    {p} .send-btn {{
        background: {theme.accent_btn};
        color: {theme.accent_btn_text};
    }}

    {p} .send-btn:hover {{
        background: {theme.accent_btn_hover};
        color: {theme.accent_btn_text};
    }}

    {p} #input-area {{
        background: {theme.bg_app};
    }}

    {p} #input-card {{
        background: {theme.bg_card};
        border: solid {theme.border_accent};
    }}

    {p} #chat-prompt {{
        color: {theme.text_primary};
    }}

    {p} #input-hint {{
        color: {theme.text_muted};
    }}

    {p} .user-msg-card {{
        background: {theme.bg_user_card};
        border: solid {theme.border};
    }}

    {p} .user-avatar {{
        background: {theme.user_badge_bg};
        color: {theme.user_badge_text};
    }}

    {p} .user-name {{
        color: {theme.user_name};
    }}

    {p} .assistant-avatar {{
        color: {theme.accent_primary};
    }}

    {p} .assistant-name {{
        color: {theme.assistant_name};
    }}

    {p} .msg-time {{
        color: {theme.text_muted};
    }}

    {p} .msg-copy {{
        color: {theme.text_muted};
    }}

    {p} .msg-copy:hover {{
        color: {theme.accent_primary};
    }}

    {p} .code-block {{
        background: {theme.bg_code};
        border: solid {theme.border};
    }}

    {p} .code-header {{
        background: {theme.bg_card};
    }}

    {p} .code-lang {{
        color: {theme.accent_secondary};
    }}

    {p} .code-copy {{
        color: {theme.text_muted};
    }}

    {p} .code-copy:hover {{
        color: {theme.accent_primary};
    }}

    {p} #footerbar {{
        background: {theme.bg_app};
        border: none;
    }}

    {p} .footer-cmd {{
        color: {theme.accent_primary};
    }}

    {p} .footer-cmd:hover {{
        color: {theme.accent_secondary};
        text-style: bold;
    }}

    {p} .footer-hint {{
        color: {theme.text_muted};
    }}

    {p} .autocomplete-popup {{
        background: {popup_bg};
        border: solid {theme.border_accent};
    }}

    {p} .autocomplete-list {{
        background: {popup_bg};
    }}

    {p} .autocomplete-list > ListItem:hover,
    {p} .autocomplete-list > ListItem.--highlight {{
        background: {user_hover_bg};
    }}

    {p} .autocomplete-key {{
        color: {theme.accent_primary};
    }}

    {p} .autocomplete-desc {{
        color: {theme.text_secondary};
    }}

    {p} #command-palette {{
        background: {popup_bg};
        border: solid {theme.border_accent};
    }}

    {p} #command-search {{
        background: {theme.bg_app};
        color: {theme.text_primary};
    }}

    {p} #command-list {{
        background: {popup_bg};
    }}

    {p} #command-list > ListItem:hover,
    {p} #command-list > ListItem.--highlight {{
        background: {user_hover_bg};
    }}

    {p} .command-key {{
        color: {theme.accent_primary};
    }}

    {p} .command-desc {{
        color: {theme.text_secondary};
    }}

    {p} #streaming-indicator {{
        color: {theme.accent_primary};
    }}

    {p} #theme-dialog {{
        background: {popup_bg};
        border: solid {theme.border_accent};
    }}

    {p} #theme-title {{
        color: {theme.accent_primary};
    }}

    {p} #theme-list {{
        background: {popup_bg};
    }}

    {p} #theme-list > ListItem {{
        color: {theme.text_primary};
    }}

    {p} #theme-list > ListItem:hover,
    {p} #theme-list > ListItem.--highlight {{
        background: {user_hover_bg};
        color: {theme.accent_primary};
    }}

    {p} .theme-color {{
        color: {theme.text_muted};
    }}

    {p} .tool-step-card {{
        background: {user_hover_bg};
        border-left: solid {theme.accent_primary};
    }}

    {p} .tool-name {{
        color: {theme.accent_primary};
    }}

    {p} .tool-target {{
        color: {theme.accent_secondary};
    }}

    {p} .tool-output-preview {{
        color: {theme.text_secondary};
    }}

    {p} .tool-status-running {{
        color: {theme.accent_secondary};
    }}

    {p} .tool-status-done {{
        color: {"ansi_bright_green" if theme.id == "system" else "#10B981"};
    }}

    {p} .tool-status-error {{
        color: {"ansi_bright_red" if theme.id == "system" else "#EF4444"};
    }}

    {p} #topbar-mode {{
        color: {theme.accent_primary};
    }}

    {p} .topbar-mode-agent {{
        color: {"ansi_bright_yellow" if theme.id == "system" else "#FACC15"};
        text-style: bold;
    }}

    {p} .topbar-mode-chat {{
        color: {theme.text_secondary};
        text-style: bold;
    }}
    """


def generate_all_themes_css() -> str:
    return "\n".join(generate_theme_css(t) for t in THEMES.values())
