"""Local initial-ranking pipeline.

The fetch + ELO-rank steps are intentionally NOT duplicated here: run the
original `reddit_art_ranker` scripts (fetch_pushshift / fetch_reddit / rank)
to produce `reddit_rankings.db`, which is the single source of truth for the
ranking algorithm. The only new step is `publish.py`, which pushes the finished
SQLite leaderboard up to DynamoDB (+ optional S3 image mirroring).

    # 1. produce the leaderboard locally (existing module, unchanged)
    python -m reddit_art_ranker.fetch_pushshift <dump.zst> --subreddit Watercolor
    python -m reddit_art_ranker.rank --subreddit Watercolor

    # 2. publish that pool to the cloud (new). Run from inside this module dir
    #    so `shared` / `local` resolve as top-level packages:
    cd reddit_art_ranker_cloud
    python -m local.publish \\
        --sqlite ../reddit_art_ranker/reddit_rankings.db \\
        --subreddit Watercolor --pool watercolor --mirror-images
"""
