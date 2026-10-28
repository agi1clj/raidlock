from datetime import timedelta

from raidlock import cli
from raidlock.core import clock


def test_lead_lease_state_never_says_expired_in_the_future():
    later = clock.iso(clock.now() + timedelta(minutes=30))
    earlier = clock.iso(clock.now() - timedelta(minutes=30))
    assert cli._lease_state({"live": True, "expires": later}).startswith("live until ")
    assert cli._lease_state({"live": False, "expires": later}) == "expired: its session ended"
    assert cli._lease_state({"live": False, "expires": earlier}).startswith("expired at ")
    assert cli._lease_state({"live": False, "expires": None}) == "expired"
