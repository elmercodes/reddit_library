# Design tools and digital simulators

*r/homemadeTCGs · Software and tools for designing and testing cards, such as card databases, template tools, Tabletop Simulator and custom digital simulators.*

## TL;DR

Two low-scoring but friendly threads anchor this chapter: a desktop card database app (TCG Forge) and a Unity simulator for a homemade TCG. Commenters keep asking what tools others use and say building digital tooling feels daunting. For a card database, the features most often requested before anyone would switch were a built-in renderer that uses the designer's own art and frames, and Tabletop Simulator export. Support counts are small throughout, so treat these as directional signals rather than strong verdicts.

## How hot it is

- **Threads:** 2 main + 3 supporting
- **Engagement:** 152 upvotes, 66 comments (33 comments per thread)
- **Heat score:** 33.1
- **Date range:** 2026-04-15 to 2026-10-26

## Consensus

- Designers are curious about the tooling behind other people's projects. Questions about tech stacks, how a website or card database was built, and which design software was used come up even in threads that are mainly about something else. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/), [S1](https://www.reddit.com/r/homemadeTCGs/comments/1w0y1es/elemental_creatures_tcg_website_updated_with_set_2/), [S2](https://www.reddit.com/r/homemadeTCGs/comments/1xg0qb3/i_work_on_an_incremental_game_that_mimic_old/), [S3](https://www.reddit.com/r/homemadeTCGs/comments/1x7a612/got_some_test_prints_in_for_duel_eterna/))
- Building digital tools feels like a barrier for many hobby designers. Commenters described template setup as hard for non-technical groups and called programming a digital version daunting. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/), [T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))

**Agreed within a single thread:**

- Commenters liked TCG Forge's approach and its one-time price. Several said they would not switch until it could place their own art and frames onto cards or export to Tabletop Simulator. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- A couple of commenters admired the designer's willingness to code a full digital version, noting that most designers avoid it. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))

## Hot takes and splits

- No real splits in these threads.

## Design lessons

- If you build or choose a card database, pair data management with rendering, or at least a clean export path. Commenters said they would hold off on a data-only tool that cannot put their own art and frames onto cards. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- Keep card data separate from card visuals and export clean CSV. You can then data-merge into tools like nanDECK, Squib, InDesign or Affinity Publisher and swap layouts without retyping cards. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- Plan a Tabletop Simulator export or another quick playtest path early. It was named as the feature that would make people try a tool. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- Make template setup approachable for non-technical collaborators. Template building was described as the hardest base-level task for less technical groups. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/), [T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- Consider locking the ruleset with paper playtests before coding a simulator, because every rule or value change has to be mirrored in code. One commenter raised this, and the simulator's designer said their rules were already settled after extensive printed play. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- Label the numbers on cards and in the UI, and give onboarding for unusual mechanics. Viewers could not decode a resource system where actions feed the opponent from a screenshot alone. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- Proofread any card text in shared screenshots. Viewers will spot typos, and a readable layout invites more useful feedback. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))

## Business and Kickstarter lessons

- A genuinely free core tier plus a pay-once Pro tier won goodwill from hobbyists who dislike subscriptions. Say up front that the core stays free. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- Ship or announce Mac and Linux builds early. Some users skip Windows-only tools. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- Check name collisions before launch. An existing storefront and shop with the same name can bury a new tool in search results. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- Build-in-public posts attract playtesters, so include a clear call to action and a link such as a Discord for anyone who wants to test. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- A free, rules-automating simulator, possibly browser-based, can lower the barrier for new players compared with a generic tabletop sandbox. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- Expect transparency questions about AI. One commenter asked directly, and the developer disclosed that AI helped write parts of the code. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))

## Content ideas

### Debates to weigh in on

- **"Should you code a simulator before your rules are locked, or after?"** One commenter argued a simulator slows iteration and discourages rule changes. The designer countered that thousands of printed cards had already settled the rules. It's a real workflow tradeoff, though support is thin. ([T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- **"Custom digital simulator or Tabletop Simulator: which is worth a hobby designer's time?"** One thread shows a full Unity build that automates the rules, while the other treats TTS export as the deciding feature for playtesting. Both are valid paths. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/), [T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- **"Subscription or one-time price: what should indie TCG tools charge?"** Commenters praised pay-once pricing and said subscriptions put them off. Pricing is a live question for anyone selling tools to hobbyists. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))

### Recurring questions to answer

- **"Would you use a card database that can't render your cards?"** This was the most repeated request in the database thread: several commenters tied switching to a renderer that uses their own frames and art. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))
- **"How are you actually playtesting your card game digitally?"** TTS export was a deciding feature for some commenters, and others asked how to playtest a custom simulator. This is a common workflow pain point. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/), [T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/))
- **"What tools are you using to build card templates and databases?"** Tool questions come up across many threads, from simulator tech stacks to website databases and Canva layouts, and template setup was called the hardest base-level task. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/), [T2](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/), [S1](https://www.reddit.com/r/homemadeTCGs/comments/1w0y1es/elemental_creatures_tcg_website_updated_with_set_2/), [S2](https://www.reddit.com/r/homemadeTCGs/comments/1xg0qb3/i_work_on_an_incremental_game_that_mimic_old/), [S3](https://www.reddit.com/r/homemadeTCGs/comments/1x7a612/got_some_test_prints_in_for_duel_eterna/))
- **"How do you make card templates if you're not technical?"** A commenter with minimal computer skills said templates were their group's biggest hurdle, and another said other tools were too steep to learn. ([T1](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/))

## Threads

- **T1** [TCG FORGE - A TCG card database made for TCG creators by a TCG creator (Feedback appreciated)](https://www.reddit.com/r/homemadeTCGs/comments/1sqv27p/tcg_forge_a_tcg_card_database_made_for_tcg/) · 71 upvotes · 35 comments · 2026-04-20
  - *Images:* The poster is sharing their TCG card database desktop app (TCG Forge) and asking for feedback from other TCG creators.
  - ![](../../../data/images/1sqv27p/1.jpg) ![](../../../data/images/1sqv27p/2.jpg) ![](../../../data/images/1sqv27p/3.jpg)
- **T2** [My simulator is taking shape! [Sidus Proxyma]](https://www.reddit.com/r/homemadeTCGs/comments/1sm0b6x/my_simulator_is_taking_shape_sidus_proxyma/) · 81 upvotes · 31 comments · 2026-04-15

**Also relevant** (main chapter elsewhere):

- **S1** [ELEMENTAL CREATURES TCG Website Updated With Set 2!](https://www.reddit.com/r/homemadeTCGs/comments/1w0y1es/elemental_creatures_tcg_website_updated_with_set_2/) · 76 upvotes · 37 comments · 2026-08-28
- **S2** [I work on an incremental game that mimic old school TCG artstyle codes](https://www.reddit.com/r/homemadeTCGs/comments/1xg0qb3/i_work_on_an_incremental_game_that_mimic_old/) · 98 upvotes · 18 comments · 2026-10-26
- **S3** [Got some test prints in for Duel Eterna](https://www.reddit.com/r/homemadeTCGs/comments/1x7a612/got_some_test_prints_in_for_duel_eterna/) · 92 upvotes · 18 comments · 2026-10-16
