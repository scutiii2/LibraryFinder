from library_finder.core.models import Catalog, MethodRecord
from library_finder.core.search import retrieve


def _method(name, visibility="public", keywords=()):
    return MethodRecord(name, "libs.drivers", "drivers.py", 1, visibility, "TicketDriver", (), (), None, (), None, (), keywords, ())


def test_retrieve_matches_goal_terms_and_prefers_public_methods():
    catalog = Catalog(1, "libs", (), (
        _method("libs.drivers.TicketDriver.wait_for_ticket", keywords=("wait", "ticket", "element")),
        _method("libs.drivers.TicketDriver._wait_raw", "internal", ("wait", "ticket")),
    ), (), "now")

    assert [item.qualified_name for item in retrieve("wait for ticket element", catalog)] == [
        "libs.drivers.TicketDriver.wait_for_ticket", "libs.drivers.TicketDriver._wait_raw"
    ]


def test_retrieve_returns_private_only_when_it_is_the_best_match():
    catalog = Catalog(1, "libs", (), (
        _method("libs.drivers.TicketDriver._wait_raw", "internal", ("raw", "locator", "polling")),
    ), (), "now")

    assert retrieve("raw locator polling", catalog)[0].visibility == "internal"
