"""Static assertion gate suite for `quickres/webview/panel.html` (Group 3/4,
`sdd/webview-panel-brand-match`; REWRITTEN back to a corrected reality on top
of the interim corrective batch #418 -- see `sdd/webview-panel-brand-match/
design` (Engram obs #424) for the full rationale).

Root cause of THIS rewrite: batch #418 (see `sdd/webview-gui-rewrite/apply-
progress`) inverted the panel's original monochrome / zero-radius / zero-
shadow mandate into an oklch-accent, rounded-corner, shadowed design. This
file reverses that inversion back to the monochrome design, now pinned to
quickres.online's own verified hex values (design D2), with real embedded
brand fonts (design D1) and a fully monochrome, glyph-distinguished toast
system (design D5).

`panel.html` is a static asset (markup + one inline <style> + one inline
<script>), so "production code" here means the file's authored bytes --
every assertion below parses those bytes with a regex and checks a SPECIFIC
expected value, matching the design's own Testing Strategy table ("Static --
panel" row: "regex over the file").

Run scoped: `python -m pytest tests/test_panel_tokens.py -q`
"""

import re
from pathlib import Path

import pytest

PANEL_PATH = Path(__file__).resolve().parent.parent / "quickres" / "webview" / "panel.html"

# -- pinned token values (design D2) -----------------------------------------
DARK_TOKENS = {
    "--bg": "#08080a",
    "--bg-alt": "#0d0d10",
    "--line": "#232326",
    "--line-soft": "#18181b",
    "--fg": "#f2f2f0",
    "--fg-dim": "#8c8c90",
    "--fg-dimmer": "#4c4c50",
}
LIGHT_TOKENS = {
    "--bg": "#f4f4f2",
    "--bg-alt": "#ffffff",
    "--line": "#c9c9c4",
    "--line-soft": "#e2e2de",
    "--fg": "#111113",
    "--fg-dim": "#5a5a5f",
    "--fg-dimmer": "#8a8a8f",
}

FORBIDDEN_TOKENS = ["--ac", "--acbg", "--wn", "--wnbg", "--ok", "--okbg", "--er", "--erbg", "--sh"]

FONT_TOKEN_NAMES = {"--font-display", "--font-body", "--font-mono", "--font-glyph"}

CIRCULAR_SELECTORS = {".qr-monitor-notice-dot", ".qr-dialog-dot", ".qr-update-progress"}

EXPECTED_FACES = {
    ("Chakra Petch", "600"),
    ("Chakra Petch", "700"),
    ("IBM Plex Sans", "400"),
    ("IBM Plex Sans", "500"),
    ("JetBrains Mono", "400"),
    ("JetBrains Mono", "500"),
}


@pytest.fixture(scope="module")
def panel_html():
    # No try/except: a missing file SHOULD raise FileNotFoundError and fail
    # every test in this module during fixture setup.
    return PANEL_PATH.read_text(encoding="utf-8")


def _style_block(html: str) -> str:
    matches = re.findall(r"<style>(.*?)</style>", html, re.DOTALL)
    assert len(matches) == 1, f"expected exactly one <style> block, found {len(matches)}"
    return matches[0]


def _script_block(html: str) -> str:
    matches = re.findall(r"<script>(.*?)</script>", html, re.DOTALL)
    assert len(matches) == 1, f"expected exactly one <script> block, found {len(matches)}"
    return matches[0]


def _theme_block(css: str, theme: str) -> str:
    match = re.search(
        r'\[data-theme="' + theme + r'"\]\s*\{([^}]*)\}', css, re.DOTALL
    )
    assert match, f'expected a [data-theme="{theme}"] rule block'
    return match.group(1)


def _strip_at_rules(css: str) -> str:
    """Remove @font-face (single brace level -- base64 payloads never contain
    `{`/`}`) and @keyframes (two nested inner rules) blocks so a plain
    selector{...} regex can walk the remaining rules without tripping on
    nested/oversized braces."""
    css = re.sub(r"@font-face\{[^}]*\}", "", css)
    css = re.sub(r"@keyframes\s+[\w-]+\{[^{}]*\{[^{}]*\}[^{}]*\{[^{}]*\}\}", "", css)
    return css


def _srgb_to_linear(channel: int) -> float:
    c = channel / 255
    return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4


def _relative_luminance(hex_color: str) -> float:
    hex_color = hex_color.lstrip("#")
    r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    return 0.2126 * _srgb_to_linear(r) + 0.7152 * _srgb_to_linear(g) + 0.0722 * _srgb_to_linear(b)


def _contrast_ratio(hex_a: str, hex_b: str) -> float:
    la, lb = _relative_luminance(hex_a), _relative_luminance(hex_b)
    lighter, darker = max(la, lb), min(la, lb)
    return (lighter + 0.05) / (darker + 0.05)


# =============================================================================
# 1-2. Dark tokens match quickres.online byte-for-byte; light tokens are
#      independently derived (not copied/inverted from the dark-only site)
# =============================================================================


def test_01_dark_theme_tokens_exact_hex_values(panel_html):
    css = _style_block(panel_html)
    dark = _theme_block(css, "dark")
    for name, value in DARK_TOKENS.items():
        pattern = re.escape(name) + r"\s*:\s*" + re.escape(value) + r"\s*;"
        assert re.search(pattern, dark), f"{name} must equal {value} in dark theme, block: {dark}"


def test_02_light_theme_tokens_independently_derived(panel_html):
    css = _style_block(panel_html)
    light = _theme_block(css, "light")
    for name, value in LIGHT_TOKENS.items():
        pattern = re.escape(name) + r"\s*:\s*" + re.escape(value) + r"\s*;"
        assert re.search(pattern, light), f"{name} must equal {value} in light theme, block: {light}"
    for name in LIGHT_TOKENS:
        assert LIGHT_TOKENS[name] != DARK_TOKENS[name], (
            f"{name} light value must not equal the dark-only reference site's value "
            f"(light theme is independently derived, not inverted)"
        )


# =============================================================================
# 3. --fg/--fg-dim meet WCAG >=4.5:1 against --bg in BOTH themes -- computed
#    for real (plain hex, unlike the old oklch tokens this replaces)
# =============================================================================


def test_03_fg_and_fg_dim_meet_wcag_contrast_both_themes():
    for theme_name, tokens in (("dark", DARK_TOKENS), ("light", LIGHT_TOKENS)):
        bg = tokens["--bg"]
        for text_token in ("--fg", "--fg-dim"):
            ratio = _contrast_ratio(tokens[text_token], bg)
            assert ratio >= 4.5, (
                f"{theme_name} {text_token} vs --bg must be >=4.5:1 (WCAG body text), "
                f"got {ratio:.2f}:1"
            )


# =============================================================================
# 4. Zero accent/warning/ok/error/shadow hue tokens survive anywhere
# =============================================================================


def test_04_no_accent_or_shadow_hue_tokens_survive(panel_html):
    css = _style_block(panel_html)
    for token in FORBIDDEN_TOKENS:
        assert not re.search(re.escape(token) + r"\s*:", css), f"{token} must not be declared anywhere"
        assert not re.search(r"var\(" + re.escape(token) + r"\)", css), f"{token} must not be referenced anywhere"


# =============================================================================
# 5. Compact-radius convention: one global `*{border-radius:8px}` reset;
#    exactly the circular/pill selectors override to 999px; nothing else
# =============================================================================


def test_05_border_radius_scale_is_compact_except_circular_selectors(panel_html):
    css = _style_block(panel_html)
    assert re.search(
        r"\*\s*,\s*\*::before\s*,\s*\*::after\s*\{[^}]*border-radius\s*:\s*8px\b", css
    ), "expected a global *,*::before,*::after{border-radius:8px} reset"

    pill_selectors = set(re.findall(r"([.\#][\w-]+)\s*\{[^}]*border-radius\s*:\s*999px", css))
    assert pill_selectors == CIRCULAR_SELECTORS, (
        f"expected exactly the 3 circular selectors at 999px, found {pill_selectors}"
    )

    other_radii = re.findall(r"border-radius\s*:\s*(\d+)px", css)
    stray = [v for v in other_radii if v not in ("8", "999")]
    assert not stray, f"unexpected non-standard border-radius value(s): {stray}"


# =============================================================================
# 6. Zero box-shadow anywhere (UI-3)
# =============================================================================


def test_06_zero_box_shadow_anywhere(panel_html):
    css = _style_block(panel_html)
    assert not re.search(r"box-shadow\s*:", css), "no box-shadow declaration may remain"


# =============================================================================
# 7. Exactly 4 font tokens in :root (adds --font-glyph over the prior 3);
#    --font-glyph is referenced ONLY by .qr-toast-glyph; no hardcoded stacks
#    outside :root (excluding the @font-face literal family declarations,
#    which are the base64 embedding itself, not a "hardcoded stack" leak)
# =============================================================================


def test_07_exactly_four_font_tokens_font_glyph_scoped_to_toast_glyph(panel_html):
    css = _style_block(panel_html)
    root_match = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    assert root_match, "expected a :root rule declaring the font tokens"
    root_decls = root_match.group(1)
    found = set(re.findall(r"(--font-[a-z]+)\s*:", root_decls))
    assert found == FONT_TOKEN_NAMES, f"expected exactly the 4 font tokens in :root, found {found}"

    glyph_uses = [m.start() for m in re.finditer(r"var\(--font-glyph\)", css)]
    assert glyph_uses, "expected --font-glyph to be referenced at least once"
    for pos in glyph_uses:
        preceding = css[:pos]
        last_open = preceding.rfind("{")
        selector = preceding[preceding.rfind("}", 0, last_open) + 1 : last_open].strip()
        assert selector == ".qr-toast-glyph", f"--font-glyph used outside .qr-toast-glyph: {selector!r}"

    non_fontface = re.sub(r"@font-face\{[^}]*\}", "", css)
    outside_root = non_fontface[: root_match.start()] + non_fontface[root_match.end() :]
    hardcoded = []
    for m in re.finditer(r"font-family\s*:\s*([^;}]+?)\s*(?:;|\})", outside_root):
        value = m.group(1).strip()
        if not value.startswith("var(--font-"):
            hardcoded.append(value)
    assert not hardcoded, f"hardcoded font-family stack(s) outside :root: {hardcoded}"


# =============================================================================
# 8. Exactly the 6 pinned (family, weight) faces are embedded as base64 woff2
#    data URIs -- no network URL survives in any of them
# =============================================================================


def test_08_six_embedded_base64_font_face_blocks(panel_html):
    css = _style_block(panel_html)
    blocks = re.findall(r"@font-face\{([^}]*)\}", css)
    assert len(blocks) == 6, f"expected exactly 6 @font-face blocks, found {len(blocks)}"
    found = set()
    for block in blocks:
        family_m = re.search(r"font-family:'([^']+)'", block)
        weight_m = re.search(r"font-weight:(\d+)", block)
        assert family_m and weight_m, f"malformed @font-face block: {block[:120]}..."
        found.add((family_m.group(1), weight_m.group(1)))
        assert "src:url(data:font/woff2;base64," in block, "font must be embedded as a base64 woff2 data URI"
        assert "format('woff2')" in block
        assert "gstatic" not in block and "http" not in block, "no network URL may remain in an embedded font"
    assert found == EXPECTED_FACES, f"expected the 6 pinned (family, weight) pairs, found {found}"


# =============================================================================
# 9. CSP allows `font-src data:` (load-bearing for the embedded fonts to
#    actually load under `default-src 'none'`), offline-only otherwise
# =============================================================================


def test_09_csp_meta_tag_allows_data_fonts(panel_html):
    meta_match = re.search(
        r'<meta\s+http-equiv="Content-Security-Policy"\s+content="([^"]*)"', panel_html
    )
    assert meta_match, "missing <meta http-equiv=\"Content-Security-Policy\"> tag"
    content = meta_match.group(1)
    assert "default-src 'none'" in content, f"CSP must start from default-src 'none', got: {content}"
    assert "font-src data:" in content, f"CSP must allow font-src data: for embedded fonts, got: {content}"


# =============================================================================
# 10. Every CSS url() is a data: URI -- no remote scheme anywhere in <style>
# =============================================================================


def test_10_no_non_data_scheme_in_any_css_url(panel_html):
    css = _style_block(panel_html)
    for m in re.finditer(r"url\(([^)]*)\)", css):
        value = m.group(1)
        assert value.startswith("data:"), f"every CSS url() must be a data: URI, found: {value[:60]}"


# =============================================================================
# 11. Bundled-weight invariant (design D1 gate): no font-weight outside
#     {400,500} for body/mono or {600,700} for display -- and every 600/700
#     declaration must live in a rule that also references var(--font-display)
# =============================================================================


def test_11_bundled_weight_invariant(panel_html):
    css = _style_block(panel_html)
    stripped = _strip_at_rules(css)
    checked = 0
    for m in re.finditer(r"([^{}]+)\{([^{}]*)\}", stripped):
        selector, body = m.group(1).strip(), m.group(2)
        weight_m = re.search(r"font-weight\s*:\s*(\d+)", body) or re.search(r"font:\s*(\d+)\s", body)
        if not weight_m:
            continue
        checked += 1
        weight = weight_m.group(1)
        if weight in ("600", "700"):
            assert "var(--font-display)" in body, (
                f"selector {selector!r} uses weight {weight} but does not reference "
                f"var(--font-display): {body}"
            )
        else:
            assert weight in ("400", "500"), f"unexpected font-weight {weight} in {selector!r}: {body}"
    assert checked > 0, "expected at least one font-weight declaration to check"


# =============================================================================
# 12. Toasts are fully monochrome; ok/err diverge via a non-color cue
#     (D5: left-rule color + font-weight + glyph), never via hue
# =============================================================================


def test_12_monochrome_toast_non_color_distinguishing_cue(panel_html):
    css = _style_block(panel_html)
    ok_match = re.search(r"\.qr-toast-ok\s*\{([^}]*)\}", css)
    err_match = re.search(r"\.qr-toast-err\s*\{([^}]*)\}", css)
    assert ok_match and err_match, "expected .qr-toast-ok and .qr-toast-err rules"
    ok_body, err_body = ok_match.group(1), err_match.group(1)

    for body in (ok_body, err_body):
        assert not re.search(r"var\(--(ok|er|ac|wn)\b", body), f"hue token leaked into toast styling: {body}"

    assert ok_body.strip() != err_body.strip(), "ok/err toast rules must diverge on a non-color cue"
    assert "font-weight" in err_body and "font-weight" not in ok_body, (
        "err toast must carry a weight cue that ok does not (D5)"
    )


def test_13_toast_glyph_map_present_in_script(panel_html):
    js = _script_block(panel_html)
    glyph_map = re.search(r"TOAST_GLYPH\s*=\s*\{([^}]*)\}", js)
    assert glyph_map, "expected a TOAST_GLYPH map in the script"
    body = glyph_map.group(1)
    assert "ok:" in body and "err:" in body and "idle:" in body
    glyphs = re.findall(r"'([^']+)'", body)
    assert len(glyphs) == 3 and len(set(glyphs)) == 3, f"expected 3 distinct glyph characters, found {glyphs}"


# =============================================================================
# 13. Real QuickRes.png logo image, theme-scoped invert filter (unchanged
#     engineering constraint regardless of visual design)
# =============================================================================


def test_14_logo_image_and_light_theme_invert_filter(panel_html):
    img_tags = re.findall(r"<img\b[^>]*>", panel_html)
    logo_tags = [t for t in img_tags if 'class="qr-logo-mark"' in t and 'src="QuickRes.png"' in t]
    assert logo_tags, "expected <img class=\"qr-logo-mark\" src=\"QuickRes.png\"> in panel.html"

    css = _style_block(panel_html)
    light_invert = re.search(
        r'\[data-theme="light"\]\s*\.qr-logo-mark\s*\{[^}]*filter\s*:\s*invert\(1\)', css
    )
    assert light_invert, "light theme must apply filter:invert(1) to .qr-logo-mark"
    dark_invert = re.search(
        r'\[data-theme="dark"\]\s*\.qr-logo-mark\s*\{[^}]*filter\s*:\s*invert', css
    )
    assert not dark_invert, "dark theme must not also invert the logo (asset is dark-native)"


# =============================================================================
# 14. z-index token scale: base < overlay < toast, --z-toast > --z-overlay
#     (unchanged engineering constraint)
# =============================================================================


def test_15_z_index_token_scale_ordering(panel_html):
    css = _style_block(panel_html)
    root_match = re.search(r":root\s*\{([^}]*)\}", css, re.DOTALL)
    assert root_match
    tokens = dict(re.findall(r"(--z-[a-z]+)\s*:\s*(\d+)\s*;", root_match.group(1)))
    assert "--z-overlay" in tokens, "missing --z-overlay token"
    assert "--z-toast" in tokens, "missing --z-toast token"
    assert int(tokens["--z-toast"]) > int(tokens["--z-overlay"]), (
        f"--z-toast ({tokens['--z-toast']}) must be greater than --z-overlay ({tokens['--z-overlay']})"
    )
    if "--z-base" in tokens:
        assert int(tokens["--z-overlay"]) > int(tokens["--z-base"])
        assert int(tokens["--z-base"]) == 0


# =============================================================================
# 15. Toast auto-dismiss timers: 4000ms ok/idle, 8000ms err (unchanged)
# =============================================================================


def test_16_toast_timing_ok_idle_4000_err_8000(panel_html):
    js = _script_block(panel_html)
    assert re.search(r"\b4000\b", js), "expected a 4000ms auto-dismiss timer for ok/idle toasts"
    assert re.search(r"\b8000\b", js), "expected an 8000ms auto-dismiss timer for err toasts"


# =============================================================================
# 16. Exactly 1 pywebview.api occurrence; safe onunhandledrejection backstop
#     (unchanged engineering constraint)
# =============================================================================


def test_17_single_pywebview_api_call_site_and_safe_rejection_handler(panel_html):
    occurrences = re.findall(r"pywebview\.api", panel_html)
    assert len(occurrences) == 1, (
        f"expected exactly 1 pywebview.api occurrence (inside the shared call() "
        f"helper), found {len(occurrences)}"
    )
    assert "onunhandledrejection" in panel_html, "missing window.onunhandledrejection backstop"
    assert "preventDefault" not in panel_html, (
        "onunhandledrejection must NOT call preventDefault() per D2/UI-9"
    )


# =============================================================================
# 17. Zero literal z-index values -- every declaration is var(--z-*)
#     (unchanged engineering constraint)
# =============================================================================


def test_18_no_literal_z_index_values(panel_html):
    css = _style_block(panel_html)
    literals = []
    for m in re.finditer(r"z-index\s*:\s*([^;}]+?)\s*(?:;|\})", css):
        value = m.group(1).strip()
        if not value.startswith("var(--z-"):
            literals.append(value)
    assert not literals, f"literal z-index value(s) found (must be var(--z-*)): {literals}"


# =============================================================================
# 18. #qr-toast-host is a direct child of <body> and the last RENDERED child
#     (unchanged engineering constraint)
# =============================================================================


def test_19_toast_host_is_last_rendered_body_child(panel_html):
    body_match = re.search(r"<body[^>]*>(.*)</body>", panel_html, re.DOTALL)
    assert body_match, "expected a <body> element"
    body = body_match.group(1)

    host_match = re.search(r'<div id="qr-toast-host"[^>]*>\s*</div>', body)
    assert host_match, 'expected an empty <div id="qr-toast-host"></div> in <body>'

    remainder = body[host_match.end() :]
    # <datalist> and <script> never render/paint, so they may trail the
    # toast host without affecting stacking-context sibling order. Any OTHER
    # element tag opening after the toast host is a violation.
    stray = re.search(r"<(?!datalist\b|/datalist\b|script\b|/script\b)[a-zA-Z]", remainder)
    assert not stray, f"an element renders after #qr-toast-host: {remainder[:200]!r}"


# =============================================================================
# 19. No http(s):// network references in any src/href attribute (offline)
#     (unchanged)
# =============================================================================


def test_20_no_remote_network_references_in_src_or_href(panel_html):
    remote = []
    for m in re.finditer(r'\b(?:src|href)\s*=\s*"([^"]*)"', panel_html):
        value = m.group(1)
        if re.match(r"^https?://", value):
            remote.append(value)
    assert not remote, (
        f"src/href must never reference a remote URL directly (panel.html loads "
        f"over file:// and must work fully offline; external links route through "
        f"call('open_external', url) instead): {remote}"
    )


# =============================================================================
# 20. Hotkey native/stretched inputs must NOT be statically `required` --
#     first-run defaults are filled client-side in boot(), never enforced by
#     a blocking static HTML5 `required` attribute on an empty value.
# =============================================================================


def test_21_hotkey_native_stretched_selects_preserve_saved_values(panel_html):
    for select_id in ("qr-hotkey-native", "qr-hotkey-stretched"):
        tag_match = re.search(r'<select\b[^>]*id="' + select_id + r'"[^>]*>', panel_html)
        assert tag_match, f"expected a <select id=\"{select_id}\"> in panel.html"
        assert "required" not in tag_match.group(0), (
            f"#{select_id} must not be statically required -- boot() applies defaults"
        )
    js = _script_block(panel_html)
    assert "[state.hotkey.native_res, state.hotkey.stretched_res]" in js
    assert "Keep that" in js and "configuration" in js


# =============================================================================
# 21. RES-2/D6: custom-resolution chip list is retired entirely. No
#     `add_custom`/`remove_custom` bridge calls or chip-row DOM/CSS survive;
#     the Apply flow routes through `pick_resolution` like a preset click.
# =============================================================================


def test_22_custom_resolution_chip_ui_fully_removed(panel_html):
    assert "add_custom" not in panel_html, "add_custom must no longer be called from panel.html"
    assert "remove_custom" not in panel_html, "remove_custom must no longer be called from panel.html"
    for needle in ("qr-chip-row", "qr-custom-chips", "qr-custom-count", "qr-chip-remove"):
        assert needle not in panel_html, f"{needle} must be fully removed from panel.html"

    js = _script_block(panel_html)
    assert "pick_resolution" in js, "custom Apply must route through the existing pick_resolution call"
    apply_branch = re.search(
        r"e\.target\.id === 'qr-custom-apply'[\s\S]{0,400}", js
    )
    assert apply_branch, "expected a qr-custom-apply click branch in the script"
    assert "pick_resolution" in apply_branch.group(0), (
        "the qr-custom-apply branch must call pick_resolution, not a bespoke custom-res API"
    )
    assert 'class="qr-btn-solid" id="qr-custom-apply"' in panel_html, (
        "custom Apply button must use the renamed qr-btn-solid class (D3 #6)"
    )


# =============================================================================
# 22. Corrective batch `webview-security-reliability-fixes` (Stream 3) --
#     readability/i18n refactors only, no runtime behavior change.
# =============================================================================


def test_23_pending_state_reset_extracted_to_shared_helper(panel_html):
    # Round 27 (Reliability finding, HIGH): qr-notice-keep/qr-notice-revert
    # no longer call resetPendingState() with a hardcoded empty outcome
    # list -- that silently dropped a still-pending sibling target from a
    # merged crash-recovery record. They now call refreshPendingAfterResolution(),
    # which re-queries the server for the real current pending state instead
    # of assuming it. resetPendingState() itself remains as the shared
    # helper for qr-notice-force-unlock, which already has the real outcome
    # list available from force_unlock_pending()'s own response.
    js = _script_block(panel_html)
    assert re.search(r"function\s+resetPendingState\s*\(", js), (
        "expected a shared resetPendingState() helper function"
    )
    reset_call_sites = re.findall(r"(?<!function )resetPendingState\(", js)
    assert len(reset_call_sites) == 1, (
        f"expected resetPendingState() called from exactly 1 handler "
        f"(qr-notice-force-unlock), found {len(reset_call_sites)}"
    )
    assert re.search(r"function\s+refreshPendingAfterResolution\s*\(", js), (
        "expected a shared refreshPendingAfterResolution() helper function"
    )
    refresh_call_sites = re.findall(r"(?<!function )refreshPendingAfterResolution\(", js)
    assert len(refresh_call_sites) == 2, (
        f"expected refreshPendingAfterResolution() called from exactly 2 handlers "
        f"(qr-notice-keep, qr-notice-revert), found {len(refresh_call_sites)}"
    )


def test_24_monitor_refresh_after_mutation_extracted_to_shared_helper(panel_html):
    js = _script_block(panel_html)
    assert re.search(r"function\s+refreshMonitorsAfterMutation\s*\(", js), (
        "expected a shared refreshMonitorsAfterMutation() helper function"
    )
    call_sites = re.findall(r"(?<!function )refreshMonitorsAfterMutation\(", js)
    assert len(call_sites) == 2, (
        f"expected refreshMonitorsAfterMutation() called from exactly 2 handlers "
        f"(per-monitor toggle, qr-monitors-disable-all), found {len(call_sites)}"
    )


def test_25_modal_head_builder_extracted_single_qr_modal_close_literal(panel_html):
    js = _script_block(panel_html)
    assert re.search(r"function\s+buildModalHead\s*\(", js), (
        "expected a shared buildModalHead() helper function"
    )
    id_literals = re.findall(r"id\s*=\s*'qr-modal-close'", js)
    assert len(id_literals) == 1, (
        f"expected exactly 1 'qr-modal-close' id literal (declared once in the "
        f"shared helper), found {len(id_literals)}"
    )
    for fn_name in ("faqModalMarkup", "monitorsModalMarkup"):
        fn_match = re.search(r"function\s+" + fn_name + r"\s*\([\s\S]*?\n}\n", js)
        assert fn_match, f"expected {fn_name} function"
        assert "buildModalHead(" in fn_match.group(0), f"{fn_name} must call buildModalHead()"


def test_26_preset_kind_labels_use_i18n_strings(panel_html):
    js = _script_block(panel_html)
    res_section = re.search(r"function renderPresetCards[\s\S]*?\n}\n", js)
    assert res_section, "expected renderPresetCards function"
    body = res_section.group(0)
    for key in ("preset_kind_native", "preset_kind_stretched", "preset_kind_low"):
        assert re.search(r"\bs\." + key + r"\b", body), (
            f"expected renderPresetCards to read {key} from state.strings, matching "
            f"how every other visible chrome label goes through s.strings"
        )


# =============================================================================
# 27-28. Corrective batch `webview-security-reliability-fixes` (Stream 4,
#     round 2) -- pure cleanup, no runtime behavior change.
# =============================================================================


def test_27_string_targets_has_no_dead_null_entry(panel_html):
    js = _script_block(panel_html)
    targets = re.search(r"const STRING_TARGETS = \{([\s\S]*?)\n\};", js)
    assert targets, "expected a STRING_TARGETS object literal"
    assert "null" not in targets.group(1), (
        "STRING_TARGETS must not contain a dead null-valued entry -- "
        "renderStrings()'s loop unconditionally skips falsy keys, and "
        "qr-custom-input's real translation is already applied separately "
        "via el('qr-custom-input').placeholder"
    )
    assert "'qr-custom-input'" not in targets.group(1), (
        "qr-custom-input must not appear in STRING_TARGETS since it is "
        "translated via its own dedicated placeholder assignment"
    )


def test_28_data_external_link_handling_extracted_to_shared_helper(panel_html):
    js = _script_block(panel_html)
    assert re.search(r"function\s+\w+\s*\([^)]*\)\s*\{\s*\n\s*const ext = e\.target\.closest\('\[data-external\]'\)", js), (
        "expected a shared helper function that owns the "
        "e.target.closest('[data-external]') check"
    )
    call_sites = re.findall(r"e\.target\.closest\('\[data-external\]'\)", js)
    assert len(call_sites) == 1, (
        f"expected e.target.closest('[data-external]') to appear exactly once "
        f"(inside the shared helper, not duplicated per handler), found {len(call_sites)}"
    )

    titlebar_handler = re.search(
        r"el\('qr-titlebar'\)\.addEventListener\('click', async function \(e\) \{([\s\S]*?)\n\}\);",
        js,
    )
    assert titlebar_handler, "expected the qr-titlebar click handler"
    actionbar_handler = re.search(
        r"el\('qr-actionbar'\)\.addEventListener\('click', async function \(e\) \{([\s\S]*?)\n\}\);",
        js,
    )
    assert actionbar_handler, "expected the qr-actionbar click handler"
    for name, handler in (("qr-titlebar", titlebar_handler), ("qr-actionbar", actionbar_handler)):
        assert "closest('[data-external]')" not in handler.group(1), (
            f"the {name} handler must delegate external-link handling to the "
            f"shared helper instead of inlining the closest('[data-external]') check"
        )


# =============================================================================
# 29-30. Corrective batch `webview-security-reliability-fixes` (Stream F,
#     round 3) -- dead openModal('notfound') branch removed; CUSTOM_RE kept
#     in sync (char-for-char) with bridge.py's _RES_TEXT_RE, cross-referenced
#     in both files.
# =============================================================================


def test_29_open_modal_has_no_dead_notfound_branch(panel_html):
    js = _script_block(panel_html)
    fn_match = re.search(r"function openModal\(kind\) \{[\s\S]*?\n\}\n", js)
    assert fn_match, "expected an openModal(kind) function"
    body = fn_match.group(0)
    assert "=== 'notfound'" not in body, (
        "openModal() must not branch on kind === 'notfound' -- that dialog is "
        "built entirely by the separate openVendorDialog() function, which "
        "sets S.modal = 'notfound' directly without ever calling "
        "openModal('notfound')"
    )
    # No caller anywhere in the script may pass 'notfound' to openModal either.
    assert not re.search(r"openModal\(\s*'notfound'\s*\)", js), (
        "no call site may invoke openModal('notfound') -- openVendorDialog() "
        "owns that dialog independently"
    )


def test_30_custom_re_matches_res_text_re_and_cross_references_it(panel_html):
    from quickres.webview.bridge import _RES_TEXT_RE

    js = _script_block(panel_html)
    custom_re_match = re.search(r"const CUSTOM_RE = /(\^.*\$)/([a-z]*);", js)
    assert custom_re_match, "expected a CUSTOM_RE literal in the script"
    js_pattern, js_flags = custom_re_match.group(1), custom_re_match.group(2)

    assert js_pattern == _RES_TEXT_RE.pattern, (
        f"panel.html's CUSTOM_RE source ({js_pattern!r}) must match bridge.py's "
        f"_RES_TEXT_RE.pattern ({_RES_TEXT_RE.pattern!r}) char-for-char -- "
        f"bridge.py is the server-side source of truth for WxH acceptance"
    )
    assert "i" not in js_flags, (
        "CUSTOM_RE must not carry the case-insensitive /i flag -- _RES_TEXT_RE "
        "has no re.IGNORECASE, so a case-insensitive JS pattern would accept "
        "text (e.g. '800X600') the Python side would reject"
    )

    # Cross-reference comments must exist on both sides so a future edit to
    # either pattern is flagged to keep the other in sync.
    panel_src = PANEL_PATH.read_text(encoding="utf-8")
    custom_re_pos = panel_src.index("const CUSTOM_RE")
    preceding_panel = panel_src[max(0, custom_re_pos - 400) : custom_re_pos]
    assert "_RES_TEXT_RE" in preceding_panel and "bridge.py" in preceding_panel, (
        "expected a comment above CUSTOM_RE cross-referencing bridge.py's "
        "_RES_TEXT_RE and noting they must be kept in sync"
    )

    bridge_path = Path(__file__).resolve().parent.parent / "quickres" / "webview" / "bridge.py"
    bridge_src = bridge_path.read_text(encoding="utf-8")
    res_text_re_pos = bridge_src.index("_RES_TEXT_RE = re.compile")
    preceding_bridge = bridge_src[max(0, res_text_re_pos - 400) : res_text_re_pos]
    assert "CUSTOM_RE" in preceding_bridge and "panel.html" in preceding_bridge, (
        "expected a comment above _RES_TEXT_RE cross-referencing panel.html's "
        "CUSTOM_RE and noting they must be kept in sync"
    )


# =============================================================================
# 31-34. Corrective batch `webview-security-reliability-fixes` (round 5):
#     boot() had no retry/fallback UI when get_initial_state fails -- the app
#     was left permanently blank with only a fading toast. Adds a persistent
#     (non-auto-dismissing) inline error state inside #qr-panel with a Retry
#     button that re-invokes boot().
# =============================================================================


def test_31_boot_error_markup_present_hidden_inside_qr_panel(panel_html):
    assert re.search(r'<div class="qr-boot-error" id="qr-boot-error" hidden>', panel_html), (
        "expected a hidden #qr-boot-error container as the persistent boot-failure state"
    )
    retry_tag = re.search(r'<button[^>]*id="qr-boot-retry"[^>]*>', panel_html)
    assert retry_tag, "expected a #qr-boot-retry button"
    assert 'class="qr-btn-solid"' in retry_tag.group(0), (
        "retry button should reuse the existing qr-btn-solid design-system button style"
    )

    panel_match = re.search(r'<main id="qr-panel">(.*?)</main>', panel_html, re.DOTALL)
    assert panel_match, 'expected <main id="qr-panel">...</main>'
    assert 'id="qr-boot-error"' in panel_match.group(1), "#qr-boot-error must live inside #qr-panel"


def test_32_boot_error_css_visibility_toggle(panel_html):
    css = _style_block(panel_html)
    assert re.search(r"\.qr-boot-error\s*\{[^}]*display\s*:\s*none", css), (
        "expected .qr-boot-error hidden (display:none) by default"
    )
    assert re.search(r"\.qr-boot-error-visible\s*\{[^}]*display\s*:", css), (
        "expected a .qr-boot-error-visible toggle class that makes it visible"
    )


def test_33_boot_error_strings_routed_through_string_targets(panel_html):
    js = _script_block(panel_html)
    targets = re.search(r"const STRING_TARGETS = \{([\s\S]*?)\n\};", js)
    assert targets, "expected a STRING_TARGETS object literal"
    body = targets.group(1)
    assert re.search(r"'qr-boot-retry'\s*:\s*'btn_retry'", body), (
        "expected qr-boot-retry routed through STRING_TARGETS to the btn_retry i18n key"
    )
    assert re.search(r"'qr-str-boot-error-title'\s*:\s*'boot_error_title'", body), (
        "expected the boot-error title routed through STRING_TARGETS to boot_error_title"
    )
    assert re.search(r"'qr-str-boot-error-text'\s*:\s*'boot_error_body'", body), (
        "expected the boot-error body routed through STRING_TARGETS to boot_error_body"
    )


def test_34_boot_shows_persistent_error_and_retry_reinvokes_boot(panel_html):
    js = _script_block(panel_html)
    assert re.search(r"function\s+showBootError\s*\(\s*\)\s*\{", js), "expected a showBootError() helper"
    assert re.search(r"function\s+hideBootError\s*\(\s*\)\s*\{", js), "expected a hideBootError() helper"

    boot_fn = re.search(r"async function boot\(\) \{[\s\S]*?\n\}\n", js)
    assert boot_fn, "expected an async function boot() {...}"
    body = boot_fn.group(0)
    assert "hideBootError()" in body, "boot() must clear the persistent error state once it succeeds"

    fail_branch = re.search(r"if \(!initial\)\s*\{([^}]*)\}", body)
    assert fail_branch and "showBootError()" in fail_branch.group(1), (
        "boot() must call showBootError() when get_initial_state fails, before returning"
    )

    assert re.search(r"id === 'qr-boot-retry'[\s\S]{0,120}\bboot\(\)", js), (
        "expected a click handler that re-invokes boot() when #qr-boot-retry is clicked"
    )


# =============================================================================
# 35-40. Corrective batch `webview-security-reliability-fixes` (round 6,
#     Stream 2): the Updates footer button (qr-check-updates) is wired to the
#     real auto-update UI flow. It calls the enhanced check_updates(), and
#     when the response indicates a genuinely available update, opens a
#     modal (reusing openModal()/buildModalHead()/the overlay backdrop
#     pattern) with an "Update Now" button that forwards confirm_update the
#     FULL check_updates() response (so the bridge-side sha256 verification
#     added in round 4 can actually engage) and a "Later" button that just
#     closes the dialog. The prior no-update toast behavior is unchanged.
# =============================================================================


def test_35_update_modal_markup_function_reuses_build_modal_head(panel_html):
    js = _script_block(panel_html)
    fn_match = re.search(r"function\s+updateModalMarkup\s*\([\s\S]*?\n\}\n", js)
    assert fn_match, "expected an updateModalMarkup() function"
    assert "buildModalHead(" in fn_match.group(0), (
        "updateModalMarkup() must reuse the shared buildModalHead() helper, "
        "matching faqModalMarkup()/monitorsModalMarkup()"
    )


def test_36_open_modal_handles_update_kind(panel_html):
    js = _script_block(panel_html)
    fn_match = re.search(r"function openModal\(kind\) \{[\s\S]*?\n\}\n", js)
    assert fn_match, "expected an openModal(kind) function"
    body = fn_match.group(0)
    assert "=== 'update'" in body, (
        "openModal() must gain an 'update' branch that appends "
        "updateModalMarkup() to the overlay, matching the existing "
        "'faq'/'monitors' branches"
    )
    assert "updateModalMarkup(" in body


def test_37_check_updates_click_opens_modal_only_when_available(panel_html):
    js = _script_block(panel_html)
    branch = re.search(
        r"if \(e\.target\.id === 'qr-check-updates'\) \{([\s\S]*?)\n  \}\n", js
    )
    assert branch, "expected the qr-check-updates click branch in the actionbar handler"
    body = branch.group(1)
    assert "call('check_updates')" in body
    assert "openModal('update')" in body, (
        "an available update must open the update modal, not just toast"
    )
    assert "pushStatus" in body, (
        "the no-update-available path must keep the existing toast behavior"
    )


def test_38_update_now_starts_background_download_with_full_update_info(panel_html):
    js = _script_block(panel_html)
    assert "e.target.id === 'qr-update-now' || e.target.id === 'qr-update-retry'" in js
    assert "await startUpdateDownload();" in js
    start_fn = re.search(r"async function startUpdateDownload\(\) \{([\s\S]*?)\n\}", js)
    assert start_fn, "expected startUpdateDownload() helper"
    body = start_fn.group(1)
    assert "call('start_update', S.updateInfo.download_url, S.updateInfo)" in body
    assert "startUpdatePolling();" in body


def test_39_update_later_closes_overlay(panel_html):
    js = _script_block(panel_html)
    assert re.search(
        r"e\.target\.id === 'qr-update-later'[\s\S]{0,80}closeOverlay\(\)", js
    ), "expected #qr-update-later to close the overlay without any bridge call"


def test_40_update_modal_strings_use_i18n_not_hardcoded_english(panel_html):
    js = _script_block(panel_html)
    fn_match = re.search(r"function\s+updateModalMarkup\s*\([\s\S]*?\n\}\n", js)
    assert fn_match, "expected an updateModalMarkup() function"
    body = fn_match.group(0)
    for key in ("update_available_title", "btn_update_now", "btn_later", "btn_retry_download"):
        assert re.search(r"\bs\." + key + r"\b", body) or re.search(r"\bS\.strings\." + key + r"\b", body), (
            f"expected updateModalMarkup() to read {key} from state.strings"
        )
    status_fn = re.search(r"function\s+updateStatusText\s*\([^)]*\)\s*\{[\s\S]*?\n\}", js)
    assert status_fn, "expected a translated updateStatusText() helper"
    for key in ("update_available_body", "update_downloading", "update_verifying", "update_ready", "update_installing", "update_failed"):
        assert key in status_fn.group(0), f"expected {key} in translated update status copy"


# =============================================================================
# 41-42. Corrective batch `webview-security-reliability-fixes` (round 7,
#     Stream 3): a pick_resolution failure that is NOT reason:'unsupported'
#     (e.g. the underlying Win32 mode-change call itself failing) used to be
#     silently discarded -- no toast, no dialog, nothing shown to the user.
#     Both call sites now route through a shared handlePickResolutionResult()
#     helper that surfaces that case as an error toast.
# =============================================================================


def test_41_pick_resolution_failure_surfaces_error_toast_when_not_unsupported(panel_html):
    js = _script_block(panel_html)
    fn_match = re.search(
        r"function\s+handlePickResolutionResult\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js
    )
    assert fn_match, "expected a shared handlePickResolutionResult() helper function"
    body = fn_match.group(0)

    assert "openVendorDialog(" in body, (
        "handlePickResolutionResult() must still open the vendor dialog for "
        "reason === 'unsupported'"
    )

    # The non-'unsupported' failure branch must push an error toast built
    # from the failure's own message, not silently return with no UI effect.
    else_branch = re.search(
        r"if\s*\(d\.reason === 'unsupported'\)[\s\S]*?\n(.*pushStatus\('err',[^\n]*)\n", body
    )
    assert else_branch, (
        "expected a pushStatus('err', ...) call after the 'unsupported' branch "
        "for any other pick_resolution failure reason"
    )
    assert "d.message" in else_branch.group(1), (
        "the non-unsupported failure toast must surface pick_resolution's own "
        "message field"
    )


# =============================================================================
# 43. Round 15 (Reliability finding, CRITICAL): the Keep Disabled/Revert
#     Now/Force Unlock recovery card must not gate solely on a live
#     in-process guard (S.guardRemainingS) -- a boot/crash-recovered pending
#     state has no live guard object and would otherwise never render the
#     card, including Force Unlock, leaving an IN_FLIGHT boot-armed lock
#     with no UI path to release it.
# =============================================================================


def test_43_recovery_card_gated_on_pending_outcomes_not_live_guard(panel_html):
    js = _script_block(panel_html)
    fn_match = re.search(r"function renderMonitorsModal\(\) \{[\s\S]*?\n\}\n", js)
    assert fn_match, "expected a renderMonitorsModal function"
    body = fn_match.group(0)

    assert "if (S.guardRemainingS != null) {" not in body, (
        "the recovery card must not gate solely on S.guardRemainingS -- that "
        "field is only ever populated from a live in-process "
        "PendingDisableGuard and stays null for boot/crash-recovered pending "
        "state, which has no such guard"
    )
    assert re.search(r"pendingOutcomes\.length\s*>\s*0", body), (
        "expected the card's render gate to key off actionable pending "
        "outcomes (populated by both recover_on_boot and recheck_pending, "
        "with or without a live guard)"
    )
    for needle in ("qr-notice-keep", "qr-notice-revert"):
        assert needle in body, f"expected {needle} to still render inside the gated card"

    # The countdown pill itself must degrade gracefully (hidden/empty)
    # instead of rendering "nulls" when there is no live guard.
    assert "displayRemainingS == null" in body and "countdown.hidden" in body, (
        "expected the countdown pill to hide itself when there is no live "
        "guard, rather than assuming S.guardRemainingS is always a number"
    )
    assert "countdown.textContent = S.guardRemainingS + 's';" not in body, (
        "countdown.textContent must not unconditionally concatenate "
        "S.guardRemainingS (null + 's' renders the literal text 'nulls' "
        "when there is no live guard)"
    )


def test_42_both_pick_resolution_call_sites_use_shared_result_handler(panel_html):
    js = _script_block(panel_html)
    call_sites = re.findall(r"handlePickResolutionResult\(d, width, height\);", js)
    assert len(call_sites) == 2, (
        f"expected handlePickResolutionResult(d, width, height) called from "
        f"exactly 2 handlers (preset click, qr-custom-apply click), found {len(call_sites)}"
    )
    # Neither call site may re-inline the old duplicated unsupported-only check.
    assert not re.search(r"d\.reason === 'unsupported'\) openVendorDialog", js), (
        "the inline 'if (d && d.ok === false && d.reason === ...) openVendorDialog(...)' "
        "check must be fully replaced by the shared helper, not left duplicated "
        "alongside it"
    )


# =============================================================================
# 42b. The active preset is OS state, not the last clicked card. A game or
# Windows Settings may change resolution without ever sending a panel click.
# =============================================================================


def test_42b_active_resolution_border_matches_the_current_os_snapshot(panel_html):
    js = _script_block(panel_html)
    section = re.search(r"function renderPresetCards[\s\S]*?\n}\n", js)
    assert section, "expected renderPresetCards helper"
    body = section.group(0)
    assert "const isCurrent" in body
    assert "state.currentResolution" in body
    assert "p.width === state.currentResolution.width" in body
    assert "p.height === state.currentResolution.height" in body
    assert "isCurrent ? 'qr-res-preset-native' : ''" in body, (
        "the active outline must follow an exact OS-resolution match, not p.kind from an old render"
    )


def test_42b_resolution_poll_is_quiet_and_redraws_only_preset_cards(panel_html):
    js = _script_block(panel_html)
    refresh = re.search(r"async function refreshResolutionState[\s\S]*?\n}\n", js)
    assert refresh, "expected passive resolution refresh helper"
    body = refresh.group(0)
    assert "call('get_resolution_state', CALL_QUIET)" in body
    assert "renderPresetCards(S);" in body
    assert "renderResSection(S);" not in body, (
        "background resolution checks must not reset hotkey selects or cause a broader redraw"
    )
    assert "RESOLUTION_POLL_MS = 2000" in js
    assert "setInterval(refreshResolutionState, RESOLUTION_POLL_MS)" in js


# =============================================================================
# 44-45. Round 17 finding 1 (R3 Reliability, HIGH): set_monitors_enabled's
#     per-target results (instance_id/ok/message/kind) were discarded by
#     both the per-target toggle and "Disable all" click handlers -- a
#     genuinely-failed enable/disable (e.g. a declined UAC prompt) gave the
#     user zero feedback: the monitor list just silently refreshed back to
#     its unchanged state. Both handlers now route through a shared
#     surfaceMonitorEnableFailures() helper that inspects d.results and
#     toasts an error listing every target whose outcome is a genuine
#     failure (kind === OUTCOME_GENUINE_FAILURE, mirroring monitors.py's
#     OUTCOME_GENUINE_FAILURE constant, or a plain ok === false entry).
# =============================================================================


def test_44_surface_monitor_enable_failures_helper_present_and_checks_kind(panel_html):
    js = _script_block(panel_html)
    assert re.search(r"OUTCOME_GENUINE_FAILURE\s*=\s*'genuine_failure'", js), (
        "expected an OUTCOME_GENUINE_FAILURE constant mirroring monitors.py's "
        "OUTCOME_GENUINE_FAILURE = \"genuine_failure\" outcome kind string"
    )
    fn_match = re.search(
        r"function\s+surfaceMonitorEnableFailures\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js
    )
    assert fn_match, "expected a surfaceMonitorEnableFailures() helper function"
    body = fn_match.group(0)
    assert "OUTCOME_GENUINE_FAILURE" in body, (
        "the helper must check each per-target kind against OUTCOME_GENUINE_FAILURE"
    )
    assert "=== false" in body, (
        "the helper must also treat a plain ok === false entry as a failure, "
        "not only kind === OUTCOME_GENUINE_FAILURE"
    )
    assert "pushStatus('err'" in body, (
        "a genuine per-target failure must surface via an error toast, reusing "
        "the existing pushStatus()/TOAST_MS.err mechanism"
    )


def test_45_both_set_monitors_enabled_call_sites_surface_failures(panel_html):
    js = _script_block(panel_html)
    call_sites = re.findall(r"surfaceMonitorEnableFailures\(d\.results\)", js)
    assert len(call_sites) == 2, (
        f"expected surfaceMonitorEnableFailures(d.results) called from exactly "
        f"2 handlers (per-monitor toggle, qr-monitors-disable-all), found {len(call_sites)}"
    )


# =============================================================================
# 46. Round 22 (R4 Resilience finding): the auto-revert countdown was a
#     purely client-side timer that froze at "0s" and never re-polled the
#     server. bridge.py can keep retrying the auto-revert for up to
#     _AUTO_REVERT_MAX_ATTEMPTS * _AUTO_REVERT_RETRY_DELAY_S past that point
#     (a missed/unanswered UAC prompt), so the guard can stay genuinely
#     unresolved long after the modal shows a frozen "0s", with the Force
#     Unlock button never appearing until the modal is closed and reopened.
#     The countdown must now hand off to a recheck_pending poller once it
#     reaches 0, and that poller must keep polling at a cadence -- driven by
#     setInterval, not a single one-shot re-check -- until the guard
#     actually resolves (outcomes drained or force_unlockable), cleaning up
#     on modal close.
# =============================================================================


def test_46_guard_countdown_switches_to_polling_recheck_pending_on_expiry(panel_html):
    js = _script_block(panel_html)

    assert re.search(r"let\s+guardPollTimer\s*=\s*null;", js), (
        "expected a dedicated guardPollTimer variable, mirroring the "
        "existing countdownTimer pattern, so poll cleanup is independent "
        "of the per-second countdown timer"
    )

    stop_match = re.search(r"function\s+stopGuardPolling\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js)
    assert stop_match, "expected a stopGuardPolling() cleanup function"
    assert "clearInterval(guardPollTimer)" in stop_match.group(0), (
        "stopGuardPolling must clear guardPollTimer via clearInterval"
    )

    start_match = re.search(r"function\s+startGuardPolling\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js)
    assert start_match, "expected a startGuardPolling() function"
    start_body = start_match.group(0)
    assert re.search(r"guardPollTimer\s*=\s*setInterval\(", start_body), (
        "startGuardPolling must assign guardPollTimer from setInterval so "
        "it keeps polling at a cadence rather than firing only once"
    )

    countdown_match = re.search(r"function startCountdownIfNeeded\(\) \{[\s\S]*?\n\}\n", js)
    assert countdown_match, "expected a startCountdownIfNeeded function"
    countdown_body = countdown_match.group(0)
    assert "startGuardPolling()" in countdown_body, (
        "expected startCountdownIfNeeded to hand off to startGuardPolling() "
        "once the local countdown reaches 0, instead of freezing forever "
        "at a stale '0s' while the server may still be mid-retry"
    )

    poll_fn_match = re.search(r"async function\s+pollGuardOnce\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js)
    assert poll_fn_match, "expected a pollGuardOnce() function driving each poll tick"
    poll_body = poll_fn_match.group(0)
    assert "'recheck_pending'" in poll_body, (
        "each poll tick must re-fetch recheck_pending, the same bridge op "
        "seedGuardAndTick uses on modal open"
    )
    assert "stopGuardPolling()" in poll_body, (
        "the poller must stop itself once the guard resolves -- otherwise "
        "it leaks an interval that keeps firing forever"
    )

    unresolved_match = re.search(r"function\s+guardStillUnresolved\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js)
    assert unresolved_match, "expected a guardStillUnresolved() helper"
    unresolved_body = unresolved_match.group(0)
    assert "force_unlockable" in unresolved_body and "outcomes" in unresolved_body, (
        "resolution must key off actual server state (outcomes drained or "
        "force_unlockable), not a fixed attempt count, so polling keeps "
        "going for as long as bridge.py is still retrying"
    )

    close_match = re.search(r"function closeOverlay\(\) \{[\s\S]*?\n\}\n", js)
    assert close_match, "expected a closeOverlay function"
    assert "stopGuardPolling()" in close_match.group(0), (
        "closeOverlay must stop guard polling, mirroring its existing "
        "stopCountdown() cleanup, so the interval doesn't leak past modal "
        "close"
    )


def test_46b_guard_countdown_renders_only_clamped_whole_seconds(panel_html):
    js = _script_block(panel_html)

    format_match = re.search(
        r"function\s+displayGuardRemainingS\s*\([^)]*\)\s*\{[\s\S]*?\n\}",
        js,
    )
    assert format_match, "expected displayGuardRemainingS() countdown formatter"
    format_body = format_match.group(0)
    assert "Number.isFinite(seconds)" in format_body
    assert "Math.max(0, Math.ceil(seconds))" in format_body, (
        "the countdown must round fractional bridge values up and clamp at "
        "zero, so it cannot render decimal or negative seconds"
    )

    countdown_match = re.search(r"function startCountdownIfNeeded\(\) \{[\s\S]*?\n\}\n", js)
    assert countdown_match
    countdown_body = countdown_match.group(0)
    assert "S.guardRemainingS = displayGuardRemainingS(S.guardRemainingS);" in countdown_body
    assert "S.guardRemainingS = Math.max(0, S.guardRemainingS - 1);" in countdown_body

    render_match = re.search(r"function renderMonitorsModal\(\) \{[\s\S]*?\n\}\n", js)
    assert render_match
    render_body = render_match.group(0)
    assert "const displayRemainingS = displayGuardRemainingS(S.guardRemainingS);" in render_body
    assert "countdown.textContent = displayRemainingS != null ? displayRemainingS + 's' : '';" in render_body


# =============================================================================
# 47. Round 24 finding (R4 Resilience, HIGH): a GetMessageW failure silently
#     kills the hotkey listener thread with no automatic restart and no
#     UI-visible signal -- panel.html never polled hotkey status, only
#     updating state.hotkey.running from the return values of explicit
#     start_hotkey/stop_hotkey calls. This mirrors round 22's guardPollTimer
#     pattern (client needs to notice server-side state changed without an
#     explicit user action triggering it) for the hotkey's own actual
#     liveness, polled via a new cheap bridge_op (get_hotkey_status) while
#     the UI believes a hotkey is running.
# =============================================================================


def test_47_hotkey_poll_timer_mirrors_guard_poll_timer_lifecycle(panel_html):
    js = _script_block(panel_html)

    assert re.search(r"let\s+hotkeyPollTimer\s*=\s*null;", js), (
        "expected a dedicated hotkeyPollTimer variable, mirroring the "
        "existing guardPollTimer pattern, so its cleanup is independent of "
        "the guard poller"
    )

    stop_match = re.search(r"function\s+stopHotkeyPolling\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js)
    assert stop_match, "expected a stopHotkeyPolling() cleanup function"
    assert "clearInterval(hotkeyPollTimer)" in stop_match.group(0), (
        "stopHotkeyPolling must clear hotkeyPollTimer via clearInterval"
    )

    start_match = re.search(r"function\s+startHotkeyPolling\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js)
    assert start_match, "expected a startHotkeyPolling() function"
    start_body = start_match.group(0)
    assert re.search(r"hotkeyPollTimer\s*=\s*setInterval\(", start_body), (
        "startHotkeyPolling must assign hotkeyPollTimer from setInterval so "
        "it keeps polling at a cadence, not firing only once"
    )
    assert "stopHotkeyPolling()" in start_body, (
        "startHotkeyPolling must clear any prior timer first, mirroring "
        "startGuardPolling's own stopGuardPolling() call at its top"
    )

    poll_fn_match = re.search(r"async function\s+pollHotkeyOnce\s*\([^)]*\)\s*\{[\s\S]*?\n\}\n", js)
    assert poll_fn_match, "expected a pollHotkeyOnce() function driving each poll tick"
    poll_body = poll_fn_match.group(0)
    assert "'get_hotkey_status'" in poll_body, (
        "each poll tick must call the get_hotkey_status bridge op"
    )
    assert "stopHotkeyPolling()" in poll_body, (
        "the poller must stop itself once the hotkey is no longer running -- "
        "otherwise it leaks an interval that keeps firing forever"
    )


def test_47_hotkey_start_and_stop_handlers_wire_polling_lifecycle(panel_html):
    js = _script_block(panel_html)

    start_handler = re.search(
        r"if \(e\.target\.id === 'qr-hotkey-start'\) \{[\s\S]*?\n  \}\n", js
    )
    assert start_handler, "expected the qr-hotkey-start click handler block"
    assert "startHotkeyPolling()" in start_handler.group(0), (
        "starting a hotkey must begin polling its actual liveness, mirroring "
        "how a confirmed disable begins guard polling"
    )

    stop_handler = re.search(
        r"if \(e\.target\.id === 'qr-hotkey-stop'\) \{[\s\S]*?\n  \}\n", js
    )
    assert stop_handler, "expected the qr-hotkey-stop click handler block"
    assert "stopHotkeyPolling()" in stop_handler.group(0), (
        "a user-initiated stop must stop polling immediately, not leak the "
        "interval until the next poll tick notices running=false"
    )


# =============================================================================
# 48. R4 Resilience finding: startCountdownIfNeeded() -- the only
#     path that ever reached startGuardPolling() -- returned immediately
#     whenever S.guardRemainingS was null. That field is only ever populated
#     from a LIVE self._pending_guard (armed by a disable confirmed during
#     THIS running session); a boot/crash-recovered pending record
#     (recover_on_boot's IN_FLIGHT branch) never constructs one, so
#     guard_remaining_s stays null on that path and polling never started.
#     boot() itself never called seedGuardAndTick()/startCountdownIfNeeded()
#     at all, so the user had to open the Monitors modal by hand before
#     polling could ever begin. boot() must now check the initial pending
#     state right after populating S.pending and start guard polling
#     immediately when it is still unresolved, reusing the existing
#     guardStillUnresolved()/startGuardPolling() pair rather than a parallel
#     mechanism.
# =============================================================================


def test_48_boot_starts_guard_polling_for_unresolved_initial_pending(panel_html):
    js = _script_block(panel_html)

    boot_match = re.search(r"async function boot\(\) \{[\s\S]*?\n\}\n", js)
    assert boot_match, "expected a boot() function"
    boot_body = boot_match.group(0)

    pending_idx = boot_body.index("S.pending = initial.pending;")
    assert "guardStillUnresolved()" in boot_body, (
        "boot() must check guardStillUnresolved() -- the same helper "
        "startGuardPolling() itself already uses to decide whether a guard "
        "is still actionable -- against the freshly populated S.pending, "
        "instead of requiring the user to open the Monitors modal before "
        "polling can ever begin"
    )
    guard_idx = boot_body.index("guardStillUnresolved()", pending_idx)
    assert guard_idx > pending_idx, (
        "the guardStillUnresolved() check must run AFTER S.pending is "
        "populated from the initial state, not before"
    )

    assert "startGuardPolling()" in boot_body, (
        "boot() must call startGuardPolling() directly for an unresolved "
        "boot/crash-recovered pending outcome, reusing the exact poller "
        "round 22 already built rather than inventing a parallel one"
    )
    start_idx = boot_body.index("startGuardPolling()", guard_idx)
    assert start_idx > guard_idx, (
        "startGuardPolling() must be called as a consequence of the "
        "guardStillUnresolved() check, not independently of it"
    )
