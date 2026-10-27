INSTRUCTIONS = (
    "You are condensing lessons from code reviews, rejected patches and explicit proposals. "
    "Merge duplicates, drop lessons that only describe one incident with no reusable rule, and rewrite "
    "each remaining lesson as one imperative sentence under 200 characters that names the path it applies to. "
    'Output one line per lesson in the form: raidlock note <card> "lesson: <sentence>". Use the card of the most '
    "recent source lesson. Do not invent lessons that the input does not support."
)


def prompt(lessons):
    """A prompt the user can run with any model. raidlock makes no API calls."""
    rows = [
        f"- [{x['id']}] card={x.get('card') or '-'} paths={','.join(x.get('paths') or []) or '-'} "
        f"source={x.get('source', '')}: {x.get('text', '')}"
        for x in lessons
    ]
    return INSTRUCTIONS + "\n\nLessons:\n" + ("\n".join(rows) if rows else "(none)") + "\n"
