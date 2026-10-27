def test_bell_scopes_to_realm_or_guild_and_clears_on_open(browse):
    p = browse()
    page = p.open("/")
    page.wait_for_selector("#bellMenu .bell-count")
    total = int(page.locator("#bellMenu .bell-count").inner_text())
    page.locator("#bellMenu summary").click()
    rows = page.locator("#bellMenu .bell-row")
    assert rows.count() == total > 0
    guilds = {t.split(" · ")[0] for t in page.locator("#bellMenu .bell-row .cls").all_inner_texts()}
    assert len(guilds) > 1
    page.locator("#bellMenu .bell-count").wait_for(state="detached")
    page = p.open("/pocket-notes")
    page.wait_for_selector("#bellMenu summary")
    page.locator("#bellMenu summary").click()
    texts = page.locator("#bellMenu .bell-row .cls").all_inner_texts()
    assert texts and all("acme-shop" not in t for t in texts)
    assert page.locator("#bellMenu .bell-row").first.get_attribute("href") == "/pocket-notes"
    assert p.errors == []
