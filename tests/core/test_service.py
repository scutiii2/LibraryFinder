from library_finder.core.models import Catalog, MethodRecord
from library_finder.core.service import find_methods


def _catalog():
    method = MethodRecord("libs.drivers.TicketDriver.wait_for_ticket", "libs.drivers", "driver.py", 1, "public", "TicketDriver", (), (), None, (), None, (), ("wait", "ticket"), ())
    return Catalog(1, "libs", (), (method,), (), "now")


class FakeRanker:
    def rank(self, goal, candidates):
        return {"recommendations": [{"method": "libs.fake.run", "confidence": 0.9, "reason": "bad"}]}


class UnavailableRanker:
    def rank(self, goal, candidates):
        raise RuntimeError("Laya unavailable")


class AuthFailedRanker:
    def rank(self, goal, candidates):
        raise PermissionError("model repository requires authentication")


class LowConfidenceRanker:
    def rank(self, goal, candidates):
        return {
            "recommendations": [
                {
                    "method": "libs.drivers.TicketDriver.wait_for_ticket",
                    "confidence": 0.36,
                    "reason": "Laya selected the best matching candidate.",
                }
            ]
        }


def test_service_rejects_model_method_not_in_candidates():
    result = find_methods("wait for a ticket", _catalog(), FakeRanker())
    assert result.recommendations == ()
    assert "unrecognized model method" in result.warnings


def test_service_falls_back_when_laya_is_unavailable():
    result = find_methods("wait for a ticket", _catalog(), UnavailableRanker())
    assert result.used_laya is False
    assert result.recommendations[0].method.qualified_name.endswith("wait_for_ticket")


def test_service_falls_back_when_model_loading_fails_with_an_auth_error():
    result = find_methods("wait for a ticket", _catalog(), AuthFailedRanker())

    assert result.used_laya is False
    assert result.recommendations[0].method.qualified_name.endswith("wait_for_ticket")
    assert "semantic ranking unavailable" in result.warnings[0]


def test_service_keeps_low_confidence_laya_evaluations_for_cli_reporting():
    result = find_methods("wait for a ticket", _catalog(), LowConfidenceRanker())

    assert result.used_laya is True
    assert result.recommendations[0].confidence == 0.36


def test_service_sends_every_catalog_method_to_laya():
    methods = tuple(
        MethodRecord(
            f"libs.drivers.Driver{index}.find_ticket",
            "libs.drivers",
            "driver.py",
            index,
            "public",
            None,
            (),
            (),
            None,
            (),
            None,
            (),
            ("find", "ticket"),
            (),
        )
        for index in range(13)
    )
    catalog = Catalog(1, "libs", (), methods, (), "now")

    class CapturingRanker:
        def rank(self, goal, candidates):
            self.candidates = candidates
            return {"recommendations": []}

    ranker = CapturingRanker()
    find_methods("find ticket", catalog, ranker)

    assert ranker.candidates == methods
