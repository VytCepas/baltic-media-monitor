"""The batch layer: PySpark map -> shuffle -> reduce over raw zips, then Spark SQL into silver and gold Parquet.

binaryFiles  whole zips packed into input splits       (a zip cannot be split)
flatMap      mapper.map_file: articles + local counts   (MAP with a combiner)
reduceByKey  sum counts per (day, group, domain)        (SHUFFLE + REDUCE)
DataFrame    dedupe articles, groupBy aggregates        (Spark SQL)
write        silver/articles by day, gold tables        (the action: lazy evaluation runs here)
"""

import time
from pathlib import Path
from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql import functions as F  # noqa: N812  Spark convention

from baltic.batch import mapper
from baltic.layout import Lake


def session(cores: int, memory: str = "6g") -> SparkSession:
    """Spark in local mode: one JVM, `cores` worker threads (a single-node "cluster")."""
    spark = (
        SparkSession.builder.master(f"local[{cores}]")
        .appName("baltic-etl")
        .config("spark.driver.memory", memory)
        .config("spark.sql.shuffle.partitions", 4 * cores)  # the default 200 is for real clusters
        .config("spark.ui.enabled", "false")
        .config("spark.ui.showConsoleProgress", "false")
        .getOrCreate()
    )
    spark.sparkContext.setLogLevel("WARN")
    return spark


def transform(spark: SparkSession, files: list[Path]) -> tuple[DataFrame, dict[str, DataFrame]]:
    """Raw zips -> (silver articles, {gold table name: DataFrame}); nothing runs until an action."""
    paths = ",".join(map(str, files))
    tagged = (
        spark.sparkContext.binaryFiles(paths, minPartitions=len(files))  # minPartitions is a hint
        .flatMap(lambda kv: mapper.map_file(*kv))
        .cache()  # two outputs read the same parsed records
    )
    articles = (
        spark.createDataFrame(tagged.filter(lambda t: t[0] == "article").values(), mapper.SCHEMA)
        .dropDuplicates(["id"])
        .withColumn("dt", F.to_date(F.substring("ts", 1, 8), "yyyyMMdd"))
    )
    counts = (
        tagged.filter(lambda t: t[0] == "count")
        .values()
        .reduceByKey(mapper.add_counts)
        .map(lambda kv: (*kv[0], *kv[1]))
    )
    domain_day = spark.createDataFrame(
        counts, "day string, group string, domain string, n_rows long, n_about long"
    ).select(F.to_date("day", "yyyyMMdd").alias("dt"), "group", "domain", "n_rows", "n_about")
    about = articles.where("about_baltic")
    gold = {
        # H1 attention: per outlet and day, all articles and those about the Baltics
        "domain_day": domain_day,
        # theme profile of each group's Baltic coverage
        "theme_group": about.select("group", F.explode("themes").alias("theme"))
        .groupBy("group", "theme")
        .count(),
        # 15-minute security counts per group: detector history and the stream = batch check
        "series_15m": about.where("security").groupBy("ts", "group").count(),  # written last
    }
    return articles, gold


def run(files: list[Path], cores: int, out: Lake | None, memory: str = "6g") -> dict[str, Any]:
    """Run the job; out=None measures without writing (benchmark). Returns the run's measurements."""
    t0 = time.perf_counter()
    spark = session(cores, memory)
    articles, gold = transform(spark, files)
    if out is None:
        relevant = articles.count()
        for table in gold.values():
            table.count()
    else:
        articles.write.mode("overwrite").partitionBy("dt").parquet(str(out.silver))
        for name, table in gold.items():
            table.coalesce(1).write.mode("overwrite").parquet(str(out.gold(name)))
        relevant = spark.read.parquet(str(out.silver)).count()
    rows = gold["domain_day"].agg(F.sum("n_rows")).first()
    wall_s = round(time.perf_counter() - t0, 2)
    spark.stop()
    return {
        "files": len(files),
        "rows": rows[0] if rows else 0,
        "relevant": relevant,
        "wall_s": wall_s,
    }
