import numpy as np
import pandas as pd
from fakes import Response

from baltic.ai import evaluate
from baltic.ai.match import Embedder, by_embedding, by_entities, entities, match


class EmbedHttp:
    """The embedding service: a fixed 3-d vector per known headline."""

    def __init__(self, vectors):
        self.vectors, self.calls = vectors, []

    def post(self, _url, json, headers, **_):
        self.calls.append((len(json["texts"]), headers))
        return Response(body={"vectors": [self.vectors[t] for t in json["texts"]]})


def test_embedder_batches_and_sends_a_fresh_token_per_request():
    tokens = iter(["t1", "t2"])
    http = EmbedHttp({str(i): [1.0, 0, 0] for i in range(3)})
    out = Embedder("https://svc/", token=lambda: next(tokens), batch_size=2, http=http).embed(
        ["0", "1", "2"]
    )
    assert out.shape == (3, 3)
    assert http.calls == [(2, {"Authorization": "Bearer t1"}), (1, {"Authorization": "Bearer t2"})]


def test_embedding_match_respects_the_time_window():
    s = np.array([[1.0, 0.0]])
    d = np.array([[1.0, 0.0], [0.6, 0.8]])
    assert by_embedding(np.array([0]), np.array([-100, 10]), s, d, 50)[0].tolist() == [1]
    best, cos = by_embedding(np.array([0]), np.array([-10, 10]), s, d, 50)
    assert best.tolist() == [0] and cos[0] == 1.0
    assert by_embedding(np.array([0]), np.array([500, 600]), s, d, 50)[0].tolist() == [-1]


def test_entity_match_needs_two_shared_rare_names():
    src = [{"p:A", "p:B", "o:Common"}, {"p:A"}]
    fillers = [{f"p:other{i}"} for i in range(100)]  # the 3 % rule needs a realistic corpus
    dst = [{"p:A", "p:B"}, {"p:A", "o:Common"}, *[{"o:Common"}] * 40, *fillers]
    t = np.zeros(len(dst))
    best, shared = by_entities(src, dst, np.zeros(2), t, 10)
    assert best.tolist() == [0, -1] and shared.tolist() == [2, 0]


def test_match_pairs_every_source_with_both_matchers():
    def frame(rows):
        return pd.DataFrame(rows, columns=["id", "domain", "title", "persons", "orgs", "t"])

    src = frame([["r1", "a.ru", "Drones over Vilnius", ["X", "Y"], [], 0]])
    fillers = [[f"b{i}", "c.lt", f"Sports {i}", [f"P{i}"], [], 6] for i in range(100)]
    dst = frame([["b1", "b.lt", "Dronai virs Vilniaus", ["X", "Y"], [], 5], *fillers])
    vectors = {"Drones over Vilnius": [1, 0, 0], "Dronai virs Vilniaus": [0.9, 0.436, 0]}
    vectors |= {f"Sports {i}": [0, 0, 1] for i in range(100)}
    pairs = match(src, dst, Embedder("http://local", http=EmbedHttp(vectors)))
    (row,) = pairs.to_dict("records")
    assert row["emb_title"] == row["ent_title"] == "Dronai virs Vilniaus" and row["ent_shared"] == 2
    assert entities(src) == [{"p:X", "p:Y"}]


def pairs_frame(n=200):
    rng = np.random.default_rng(1)
    return pd.DataFrame(
        {
            "src_title": [f"r{i}" for i in range(n)],
            "emb_cos": rng.uniform(0.3, 1.0, n).round(3),
            "emb_title": [f"e{i}" for i in range(n)],
            "ent_shared": rng.choice([0, 0, 2, 3], n),
            "ent_title": [f"n{i}" for i in range(n)],
        }
    )


def test_strata_compare_at_equal_coverage():
    s = evaluate.strata(pairs_frame())
    assert len(s["emb_top"]) == len(s["ent"])
    assert (
        s["emb_top"].index.tolist()
        == pairs_frame().sort_values("emb_cos", ascending=False).index[: len(s["ent"])].tolist()
    )


def test_the_sheet_is_blind_and_each_pair_is_labelled_once():
    sheet, key = evaluate.sample_pairs(pairs_frame())
    assert list(sheet.columns) == ["item", "russian_title", "baltic_title", "label"]
    assert sheet.item.is_unique and set(key.item) == set(sheet.item)
    assert len(key) >= len(sheet)  # one labelled pair may count for two strata
    assert key.groupby("stratum").size().max() <= 30


def test_score_and_h3():
    key = pd.DataFrame({"item": [1, 2, 3, 4, 5, 6], "stratum": ["emb_top"] * 3 + ["ent"] * 3})
    sheet = pd.DataFrame(
        {"item": [1, 2, 3, 4, 5, 6], "label": ["y", "Y", "yes", "n", "not sure", "  "]}
    )
    p = evaluate.score(sheet, key, by="stratum")
    assert (p["emb_top"]["k"], p["emb_top"]["n"], p["ent"]["k"], p["ent"]["n"]) == (
        3,
        3,
        0,
        1,
    )  # 'not sure' and blank are left out
    assert (
        evaluate.h3(p)["decided"] and evaluate.h3(p)["shown"] is False
    )  # 3/3 vs 0/2 overlaps at this n
    assert evaluate.h3({"ent": p["ent"]}) == {"decided": False}


def test_detector_sample_mixes_strong_alerts_with_quiet_busy_hours():
    ts = [f"202609011{m:01d}0000" for m in range(10)]  # hourly-ish strings are fine for the sampler
    table = pd.DataFrame({"ru_by": [9] * 10, "other": [0] * 10}, index=ts)
    alerts = [
        {
            "group": "ru_by",
            "slot": "2026-09-01T12:00:00",
            "observed": 40,
            "expected": 2.0,
            "score": 9.0,
        }
    ]
    arts = pd.DataFrame(
        {"ts": ts, "group": "ru_by", "title": "Drill", "about_baltic": True, "security": True}
    )
    sheet, key = evaluate.sample_slots(table, alerts, arts, n=3)
    assert key.alert.sum() == 1 and (~key.alert).sum() == 3
    assert (
        list(sheet.columns) == ["item", "group", "ts", "headlines", "label"]
        and "alert" not in sheet
    )
