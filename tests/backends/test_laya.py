from backends.laya import LayaRanker
from core.models import MethodRecord


def _method(name):
    return MethodRecord(name, "libs.drivers", "driver.py", 1, "public", None, (), (), None, (), None, (), (), ())


def test_laya_attributes_manual_text_to_the_user():
    from dataclasses import replace
    from core.models import Enrichment
    method = replace(_method("libs.find"), enrichment=Enrichment("My summary", "purpose", (), (), (), "", (), (), source="manual"), enrichment_status="success")
    class Engine:
        def predict(self, state, questions):
            text = questions["fit"]["criteria"]["true"]
            assert "User-written description: summary=My summary" in text
            assert "MiniCPM analysis" not in text
            return {"answers": {"fit": {"noul": 0.7}}}
    assert LayaRanker(Engine()).rank("find", [method])["recommendations"][0]["confidence"] == 0.7


def test_laya_ranker_accepts_each_method_with_yes_probability_above_half():
    first = _method("libs.drivers.First.find")
    second = _method("libs.drivers.Second.find")
    third = _method("libs.drivers.Third.find")

    class FakeEngine:
        def __init__(self):
            self.questions = []
            self.confidences = iter((0.51, 0.50, 0.90))

        def predict(self, state, questions):
            self.state = state
            self.questions.append(questions)
            confidence = next(self.confidences)
            return {
                "answers": {
                    "fit": {"noul": confidence},
                }
            }

    engine = FakeEngine()
    progress = []
    result = LayaRanker(engine, lambda current, total, method, confidence: progress.append((current, total, method, confidence))).rank("find a ticket", (first, second, third))

    assert len(engine.questions) == 3
    assert [item["method"] for item in result["recommendations"]] == [first.qualified_name, second.qualified_name, third.qualified_name]
    assert [item["confidence"] for item in result["recommendations"]] == [0.51, 0.50, 0.90]
    assert progress == [(1, 3, first, 0.51), (2, 3, second, 0.50), (3, 3, third, 0.90)]
