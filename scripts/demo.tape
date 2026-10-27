Output docs/assets/hero-raw.mp4

Require raidlock
Require git
Require python3

Set Shell "bash"
Set FontSize 16
Set Width 1200
Set Height 680
Set Padding 24
Set Theme "GitHub Dark"
Set TypingSpeed 40ms
Set Framerate 12
Set PlaybackSpeed 1.0
Set CursorBlink false

Hide
Type "source scripts/demo-tape-setup.sh && clear"
Enter
Sleep 2s
Show

Type "raid up"
Enter
Sleep 2s

Type 'lead new "Cart discount" --id cart-discount --locks storefront/src/cart --verify-cmd "python3 -m unittest discover -s tests"'
Enter
Sleep 1s
Type 'lead new "Tax on receipts" --id receipt-tax --locks storefront/src/cart/tax.py'
Enter
Sleep 1s
Type 'lead new "FAQ page" --id faq --locks storefront/docs/faq.md'
Enter
Sleep 1s

Hide
Type "join_agents; printf '\033[1A\033[2K'"
Enter
Sleep 2s
Show

Type "cd ~/workers/codex-1/storefront && agent codex-1 claim cart-discount"
Enter
Sleep 1500ms
Type "cd ~/shop/storefront && agent claude-1 claim receipt-tax"
Enter
Sleep 2500ms
Type "agent claude-1 claim faq"
Enter
Sleep 1500ms

Hide
Type "codex_builds && cd ~/workers/codex-1/storefront; printf '\033[1A\033[2K'"
Enter
Sleep 1s
Show

Type 'agent codex-1 move cart-discount review --note "discount.py, 2 tests pass"'
Enter
Sleep 1500ms
Type "lead done cart-discount"
Enter
Sleep 2500ms
Type "lead review cart-discount --assign claude-review"
Enter
Sleep 1s
Type 'agent claude-review review cart-discount --verdict approve --note "range check looks right"'
Enter
Sleep 1500ms
Type "lead verify cart-discount && lead done cart-discount"
Enter
Sleep 3s

Type "clear && grep -A5 '^| Configuration' $RAIDLOCK_REPO/docs/guides/benchmark.md"
Enter
Sleep 4s
