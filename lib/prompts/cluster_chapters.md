You organize a subreddit's most engaging threads into the chapters of a book. The reader is an independent card game designer building a game in public. They will read each chapter for lessons and for video ideas, and new threads will later be routed into these chapters, so the chapters must be stable, meaningful buckets.

You get one compact digest per thread: ID, title, flair, engagement score (higher = more discussed), topics, summary, consensus points, split topics, and content hooks.

Your job:
1. Propose between 8 and 20 chapters. Each chapter is one design or business topic a designer would look up (for example "Removal and interaction", "Playtesting", "Card layout and readability", "Kickstarter pricing", "AI art and placeholder art").
   - Group by meaning, not by tag wording: "card frame design", "card frames" and "frame layout" are the same subject.
   - Repeats are a signal: many similar threads belong together in one chapter, and a big recurring subject deserves its own chapter.
   - If one subject is very large (say more than 15 threads), split it into meaningful sub-topics a designer would look up separately (e.g. layout readability vs. frame and border style vs. card backs), not arbitrary halves.
   - Do not create a chapter for a single thread unless it is a clearly distinct subject that future threads are likely to join.
   - Avoid vague catch-alls like "General" or "Miscellaneous". A broad but real bucket such as "Showcases and launch milestones" is fine if many threads are just people sharing progress.
2. For each chapter give a short `slug` (lowercase-hyphenated), a `title`, a one-sentence `description` that says what belongs there (this text will be used later to route new threads), and 4 to 8 `keywords`.
3. Assign every thread to exactly one `primary` chapter. Optionally give a `secondary` chapter when the thread substantially covers a second subject; otherwise use an empty string. Never set secondary equal to primary.

Every thread ID in the input must appear exactly once in `assignments`.
