# Foresight

Foresight predicts merge conflicts against branches recorded on live leases for
the same repository, including leases in other registered workspaces.

Run it before claiming or committing:

```sh
raidlock foresight CARD
raidlock foresight CARD --json
```

Predicted conflicts produce warnings and exit 0. JSON output includes
`conflicts`, `skipped`, `checked`, `messages`, and `warnings`. Use the suggested
card and agent to coordinate before merging.

Claims automatically run the same advisory check against the requested
`--checkout` (the CLI defaults to the current directory). Human output displays
warnings; library and `--json --fields all` claim results include a `warnings`
list only when advisories are present. Minimal JSON and field selections omit
unselected warnings. Prediction
errors become warnings too. Foresight never vetoes a claim; normal authentication,
lease conflicts, and other claim preconditions still apply.

Foresight needs Git 2.38 or newer and locally available branch refs. It uses local
Git commands without fetching. It checks at most 20 live leases, with a
five-second timeout per comparison. Missing branches, timeouts, and unsupported
Git versions are reported as skipped advice. Claims can take longer while these
checks run.

Uncommitted changes in the selected checkout are included when checking its
current HEAD. Other leases use their recorded branch tips, so their uncommitted
changes are not visible. Predictions leave refs, the real index, working files,
and repository objects unchanged. They are a point-in-time estimate and do not
guarantee a later merge will be clean.
