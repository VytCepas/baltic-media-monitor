"""AI step: pair each Russian/Belarusian article about the Baltics with its closest Baltic article (+-48 h).

Two matchers, so the AI is measured against something:
- embeddings: multilingual MiniLM sentence vectors of the headlines, served by the private Cloud Run
  service (ai/embedder); the best match is the highest cosine within the time window;
- baseline: the candidate sharing most named people/organisations (>= 2, ignoring names in > 3 % of all
  articles, e.g. "Putin").
"""

from collections import Counter, defaultdict
from collections.abc import Callable

import numpy as np
import pandas as pd
import requests

from baltic import gdelt
from baltic.layout import Lake

COLUMNS = ["id", "ts", "domain", "group", "title", "persons", "orgs", "about_baltic"]
TARGETS = ("baltic", "baltic_rus")
HIGH_COS = 0.7  # "very similar": the emb_high stratum
MIN_SHARED = 2  # rare names two headlines must share to count as an entity match


class Embedder:
    """Client of the embedding service: batches of headlines in, unit-length vectors out."""

    def __init__(
        self,
        url: str,
        token: Callable[[], str] | None = None,
        batch_size: int = 256,
        http: requests.Session | None = None,
    ) -> None:
        """token: returns a fresh ID token per request (Cloud Run tokens expire after an hour)."""
        self.url, self.token, self.batch_size = url.rstrip("/"), token, batch_size
        self.http = http or requests.Session()

    def embed(self, texts: list[str]) -> np.ndarray:
        """One row per text; rows are L2-normalised, so a dot product is a cosine."""
        parts = []
        for i in range(0, len(texts), self.batch_size):
            headers = {"Authorization": f"Bearer {self.token()}"} if self.token else {}
            r = self.http.post(
                f"{self.url}/embed",
                json={"texts": texts[i : i + self.batch_size]},
                headers=headers,
                timeout=300,
            )
            r.raise_for_status()
            parts.append(np.asarray(r.json()["vectors"], dtype=np.float32))
        return np.vstack(parts)


def google_token(audience: str) -> Callable[[], str]:
    """ID tokens from the VM's metadata server (or local service-account credentials)."""
    import google.auth.transport.requests
    from google.oauth2 import id_token

    def fetch() -> str:
        request = google.auth.transport.requests.Request()
        token: str = id_token.fetch_id_token(request, audience)  # type: ignore[no-untyped-call]
        return token

    return fetch


def load(lake: Lake) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Sources (ru_by about the Baltics) and candidates (Baltic outlets), each with minutes in `t`."""
    df = pd.read_parquet(lake.silver, columns=COLUMNS)
    df = df[df.about_baltic & (df.title.str.len() > 15)].copy()
    # minutes since the epoch; as_unit: pandas 3 parses to microseconds
    df["t"] = pd.to_datetime(df.ts, format=gdelt.TS).dt.as_unit("s").astype("int64") // 60
    src = df[df.group == "ru_by"].reset_index(drop=True)
    dst = df[df.group.isin(TARGETS)].sort_values("t").reset_index(drop=True)
    return src, dst


def by_embedding(
    src_t: np.ndarray, dst_t: np.ndarray, s: np.ndarray, d: np.ndarray, window: float
) -> tuple[np.ndarray, np.ndarray]:
    """Index and cosine of each source's most similar candidate within +-window minutes (-1 = none)."""
    best, cos = np.full(len(src_t), -1), np.full(len(src_t), -1.0)
    for i, t in enumerate(src_t):
        lo = int(np.searchsorted(dst_t, t - window, side="left"))  # dst_t is sorted
        hi = int(np.searchsorted(dst_t, t + window, side="right"))
        if hi > lo:
            sims = d[lo:hi] @ s[i]
            best[i], cos[i] = lo + int(sims.argmax()), float(sims.max())
    return best, cos


def entities(df: pd.DataFrame) -> list[set[str]]:
    """Each article's named people ('p:') and organisations ('o:')."""
    return [
        {"p:" + x for x in p} | {"o:" + x for x in o}
        for p, o in zip(df.persons, df.orgs, strict=True)
    ]


def by_entities(
    src: list[set[str]], dst: list[set[str]], src_t: np.ndarray, dst_t: np.ndarray, window: float
) -> tuple[np.ndarray, np.ndarray]:
    """Index and number of shared names of each source's best candidate (>= 2 shared, else -1 / 0)."""
    common = Counter(e for es in src + dst for e in es)
    rare = 0.03 * (len(src) + len(dst))
    index = defaultdict(list)
    for j, es in enumerate(dst):
        for e in es:
            if common[e] < rare:
                index[e].append(j)
    best, shared = np.full(len(src), -1), np.zeros(len(src), int)
    for i, es in enumerate(src):
        votes = Counter(
            j
            for e in es
            if common[e] < rare
            for j in index[e]
            if abs(dst_t[j] - src_t[i]) <= window
        )
        if votes and (top := votes.most_common(1)[0])[1] >= MIN_SHARED:
            best[i], shared[i] = top
    return best, shared


def match(
    src: pd.DataFrame, dst: pd.DataFrame, embedder: Embedder, window_h: float = 48
) -> pd.DataFrame:
    """One row per source article with both matchers' best counterpart."""
    titles = sorted(set(src.title) | set(dst.title))  # embed each distinct headline once
    vectors = dict(zip(titles, embedder.embed(titles), strict=True))
    s = np.stack([vectors[x] for x in src.title])
    d = np.stack([vectors[x] for x in dst.title])
    src_t, dst_t, window = src.t.to_numpy(), dst.t.to_numpy(), window_h * 60
    emb, cos = by_embedding(src_t, dst_t, s, d, window)
    ent, shared = by_entities(entities(src), entities(dst), src_t, dst_t, window)

    def pick(column: str, j: np.ndarray) -> list[str | None]:
        return [dst[column].iloc[k] if k >= 0 else None for k in j]

    return pd.DataFrame(
        {
            "src_id": src.id,
            "src_domain": src.domain,
            "src_title": src.title,
            "emb_cos": cos.round(4),
            "emb_title": pick("title", emb),
            "emb_domain": pick("domain", emb),
            "ent_shared": shared,
            "ent_title": pick("title", ent),
            "ent_domain": pick("domain", ent),
        }
    )
