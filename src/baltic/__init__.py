"""Baltic media monitor: how Russian/Belarusian, neighbouring, other foreign and Baltic outlets cover LT/LV/EE.

A Lambda architecture over GDELT GKG 2.1 (one metadata file per feed every 15 minutes):

    article, gdelt, layout     the shared core: classify a row, talk to GDELT, where every file lives
    stream/  speed layer       producer (GDELT -> Kafka), monitor (Kafka -> slots + spike alerts), seed
    batch/   batch layer       mirror (backfill), mapper + etl (Spark map/reduce), baseline + bench (study)
    ai/                        match (embedding service vs shared entities), evaluate (hand labels)
    analysis/ serving layer    hypotheses (H1, H2), checks (reconcile, stream = batch), report (figures)
    detector, stats            the spike detector and the two statistical tools every result uses

`python -m baltic --help` lists every command.
"""
