---
title: "The mutation-testing digest for July 2026"
date: 2026-08-01T09:00:00+03:00
description: "Survivors as Work Orders, Scores Under Audit"
tags: [testing, evidence]
author: "muta"
---

A large language model (LLM) prototype killed 95.3% of manually reviewed behavior-changing survivors; a PHP library found 434 survivors behind 100% line coverage; and a broken output pipe showed how easily faulty experimental plumbing can produce spectacular results. July's prototypes posted impressive numbers; reality kept the admin password.

*Scope: work first made public in July, plus July publication events whose earlier versions are clearly labeled; late indexing remains possible.*

## Survivors became work orders

[LLMutantKiller](https://www.franktip.org/pubs/issta2026.pdf) gives an LLM the exact surviving StrykerJS mutant plus execution feedback and asks for a Jest test that kills it. The researchers sampled 915 survivors from 13 JavaScript and TypeScript projects and manually classified 815 as behavior-changing. Claude Sonnet 4.6 killed 777 of them, or **95.3%**; Devstral reached 62.4%, and Llama 3.3 reached 26.5%.

Strong result, narrow job: it does not show independent bug finding or test maintainability. The study used sampled, manually labeled survivors from one ecosystem and had no developer study. The date when it first became public is uncertain, so treat it as a July conference event.

[TATG](https://arxiv.org/abs/2607.03194) tracks branch-coverage and assertion goals that generated tests have not met, then uses survivors for another round. Across 141 Java methods, it reports gains of **20.14–37.66 percentage points** across line coverage, branch coverage, and mutation score over named baselines. Limits: one method at a time, mutation score as the endpoint, and only a partial comparison with a proprietary system.

Several July projects treated a survivor as a machine-readable ticket: write one test, prove one distinction, or explain equivalence. The red number finally got a job.

## What happened in real repositories

### One hundred percent line coverage hid 434 survivors

[Passkeys PR #43](https://github.com/shipmonk-rnd/passkeys/pull/43) started with 100% line coverage yet **434 survivors among 1,560 mutants**, a 72% covered-code score. Adding 92 tests and 178 assertions cut the open-PR snapshot to **13 among 1,548**, roughly 99%; the proposed gate is 98% because the PHP coverage engines PCOV and Xdebug differ slightly. The mutant denominators differ, so the percentages are not directly comparable.

### Cashu put mutation in every pull request, then took it back out

Cashu added a scheduled Stryker run in [#733](https://github.com/cashubtc/cashu-ts/pull/733), a pull-request gate in [#754](https://github.com/cashubtc/cashu-ts/pull/754), then removed the gate in [#773](https://github.com/cashubtc/cashu-ts/pull/773). Inherited survivors, outdated comparison baselines, and 10–90-minute runs without pull-request bot feedback weakened the signal, so scheduled runs and narrowly scoped local checks stayed.

### OpenHands chose on-demand reports

[OpenHands PR #16184](https://github.com/OpenHands/OpenHands/pull/16184), merged July 30, added on-demand reports for either the full codebase or only changed code, but no continuous-integration gate. A one-file trial killed 23 mutants; a [follow-up issue](https://github.com/OpenHands/OpenHands/issues/16195) questioned roughly 1,000 added lockfile lines. Extra dependencies and lockfile churn are also adoption costs.

### Field notes: useful, small, and correctly caveated

| Project | What happened in July | Status/lesson |
|---|---|---|
| [async-test-lib #162](https://github.com/PIsberg/async-test-lib/pull/162) | A campaign with the Java mutation tool PIT found two defects; an 80-minute runtime and inconsistent results kept the gate at 74% | Merged |
| [roastpilot-cloud #68](https://github.com/syamaner/roastpilot-cloud/pull/68) | Two control-flow survivors exposed premature-stop paths; the stored baseline needed safeguards against manual editing | Merged |
| [Mostro #849](https://github.com/MostroP2P/mostro/pull/849) | Ten Rust mutants killed; test-order and memory problems also surfaced | Open |
| [HyperDX #2755](https://github.com/hyperdxio/hyperdx/pull/2755) | Local five-file pilot found date-handling paths whose outputs tests never checked | Open |
| [Uber H3 #1202](https://github.com/uber/h3/pull/1202) | Mull proposal; reviewers questioned the cost of running it on every pull request | Open |
| [ROCm #10021](https://github.com/ROCm/rocm-libraries/pull/10021) | Reviewers requested defect records, scope, and a score | Changes requested |

A campaign using AI with the Java mutation tool PIT landed seven patches—[Google Truth](https://github.com/google/truth/pull/1674), [OpenNLP](https://github.com/apache/opennlp/pull/1135), [Tika](https://github.com/apache/tika/pull/2917), [DKPro JWPL](https://github.com/dkpro/dkpro-jwpl/pull/476), [DataStax CDM](https://github.com/datastax/cassandra-data-migrator/pull/439), [Taikai](https://github.com/enofex/taikai/pull/315), and [Cluecumber](https://github.com/trivago/cluecumber/pull/409)—while five other pull requests were closed: [AssertJ](https://github.com/assertj/assertj/pull/4310), [YDB](https://github.com/ydb-platform/ydb-java-sdk/pull/672), [ApprovalTests.Java](https://github.com/approvals/ApprovalTests.Java/pull/826), [BungeeCord](https://github.com/SpigotMC/BungeeCord/pull/4001), and [Oviva eHealthID](https://github.com/oviva-ag/ehealthid-relying-party/pull/191). Proposed tests sometimes duplicated coverage, crossed abstraction boundaries, over-specified behavior, or did not fit project style. Agents proposed; maintainers decided.

## The audit arrived with the automation

### Equivalent-mutant detection reported gains—and immediately met a tougher exam

Two July publications add program structure to learned equivalent-mutant detection:

- [SGENT](https://doi.org/10.1109/TSE.2026.3710317) compares original/mutant pairs with a [Siamese neural network](https://en.wikipedia.org/wiki/Siamese_neural_network) built from graphs of code structure, execution order, and value dependencies. On 189,424 labeled mutants from ten projects, its abstract reports **85.28% [F1](https://en.wikipedia.org/wiki/F-score)**, a score that rewards catching relevant cases while avoiding false alarms.
- [RIP-Guided Graph Evidence](https://doi.org/10.1145/3827622) gives an LLM evidence about whether a mutation is reached, changes program state, and propagates to an observable point. On roughly 189,000 Java mutants from ten projects, it reports gains of about **8–12 F1 points** over code-only prompting.

Both are promising, but datasets dominated by one answer or poorly separated training and evaluation examples can make familiar-pattern recognition look like behavioral reasoning. Four shared authors and near-identical dataset sizes make these related studies, not independent replications.

The official abstract for [“Re-evaluating Detection of Equivalent Mutants Using LLMs”](https://conf.researchr.org/details/issta-2026/issta-2026-research-papers/159/Re-evaluating-Detection-of-Equivalent-Mutants-Using-LLMs-We-Should-Properly-Measure-) then supplied the cold shower. Every evaluated method performed worse on new projects, languages, and operators. It also reports that the same Java methods appeared in both training and evaluation data, and describes a shortcut that predicts whichever class—equivalent or not—is most common for each Java method. No public full paper was available by the cutoff, so exact drops would be speculation.

A valid behavioral witness uses the public interface, passes on the original, and reliably fails on the mutant; a classifier saying “equivalent” remains a dataset-dependent triage hint.

### The score answered two different questions differently

[A large replicability study](https://arxiv.org/abs/2607.22880), with [public code and data](https://github.com/drixs2050/Cov_mut_bug_detect_correlation), analyzed more than 100,000 Java tests generated by 11 LLMs. Coverage and mutation scores could help compare generators when the supplied program was treated as correct. When tests were generated from already-buggy code, coverage did not reliably predict whether they exposed the bug; mutation score was not evaluated there because mutation analysis presupposes a passing suite.

Useful metric, different question.

### July's most useful result may be a broken pipe

[Adversarial Test-Hardening for AI-Written Code](https://arxiv.org/abs/2607.23002) used survivors to refine Python tests. Across five independent runs on each of four programs, the corrected runs killed a mean of **78.3%** of survivors, with a statistically estimated 95% uncertainty range of **59.2–93.5%**; the [protocol and code are public](https://github.com/Jott2121/crucible). An earlier spectacular effect came from silent output truncation and inconsistent sampling between experiment versions. The author fixed both and publicly specified the replacement analysis before rerunning it—because plumbing that can invent an effect is part of the experiment.

### Mure reports identical deep-neural-network results at lower cost

[Mure](https://arxiv.org/abs/2607.18582) speeds mutation testing for [deep neural networks](https://en.wikipedia.org/wiki/Deep_learning) by reusing unchanged early layers and rerunning only the affected later layers. It claims exact agreement with ordinary exhaustive execution and reports an average **44.54% cost reduction** across 15 models; the [artifact is public](https://doi.org/10.5281/zenodo.21447443). “Lossless” describes the reuse result, not the quality of the mutants or kill rule.

### July research radar

| Work | July event | Editorial read |
|---|---|---|
| [LogMorph](https://doi.org/10.4204/EPTCS.450.14) | Prolog operators derived from 200 manually classified fixes within 7,201 submissions | Good domain grounding; similar output-value distributions do not prove identical behavior. July proceedings followed a [May author page](https://pmorvalho.github.io/publications/iclp2026-2/) |
| [MuGu](https://doi.org/10.3390/app16146950) | Safety-specific mutations and prioritization | Reports a 96.3% safety mutation score and 71.8% less testing effort on the SIR and Defects4J benchmark suites, not deployed systems |
| [Machine Learning in Mutation Testing](https://doi.org/10.1016/j.procs.2026.07.048) | Systematic review | Useful eight-page map; two databases and no quantitative synthesis |
| [Neural Monte Carlo tree search for smart contracts](https://doi.org/10.1145/3828728) | July journal expansion of [earlier ASCENT work](https://doi.org/10.1007/978-3-032-12335-0_2) | Uses Monte Carlo tree search to prioritize tests; reports 28–61% fewer executions on five projects, not total cost |
| [Terraform mutation operators](https://doi.org/10.5753/reic.2026.8471) | Article based on a [2025 thesis of the same title](https://www.oasisbr.ibict.br/vufind/Author/Home?author=%22Ribeiro%2C+Isadora%22&type=Author) | Useful domain expansion; evidence is Terraform-specific |
| [Violation-Aware Mutation Testing for Deep-Learning Primitives](https://doi.org/10.2139/ssrn.7119515) | SSRN disclosure | Preprint/watch-list item, not peer-reviewed evidence |
| [Model-driven Java bytecode mutation](https://doi.org/10.1007/s10270-026-01398-2) | Journal article | Extension of [public 2024 work](https://arxiv.org/abs/2404.14097), not a new line |
| [VIZDETOUR](https://arxiv.org/abs/2607.12363) | Visualization-preserving transformations | Reports 47 new rendering bugs, 39 confirmed and 18 fixed; adjacent to ordinary score-based mutation |
| [Solidity property generation](https://arxiv.org/abs/2607.23308) | Mutation score used to evaluate generated properties | Best mean 25.99%, versus 31.75% for human properties; property generation is the contribution |
| [When Knowledge Changes](https://arxiv.org/abs/2607.26843) | Mutates retrieved documents used to help a model answer | More than 28,000 mutants; 4.9–10.2% broke expected consistency rules; adjacent domain |

Excluded from the July-first count after lineage checks: the [multilingual study](https://arxiv.org/abs/2607.00511), [Round-Trip](https://conf.researchr.org/details/icst-2026/mutation-2026-papers/7/Round-Trip-Mutation-Testing-Translating-Code-to-Natural-Language-Intent-and-back), [MuMuTestUp](https://arxiv.org/abs/2605.19265), [Test vs Mutant](https://arxiv.org/abs/2602.08146), [Argus](https://www.cs.tulane.edu/~wjiyuan/research/Argus_FSE2026.pdf), and [Intent-Based Mutation Testing](https://doi.org/10.1109/ICSTW64639.2025.10962508).

## Established tools: less spectacle, more operational fixes

[Stryker4s 1.0.0](https://github.com/stryker-mutator/stryker4s/releases/tag/v1.0.0) arrived July 15, followed by fixes and [1.1.1](https://github.com/stryker-mutator/stryker4s/releases/tag/v1.1.1) on July 30. It rewrote Maven support and added support for Mill, Scala 3.9, and Java 17 output. Maintainers report that a mutation run for one Maven module fell from roughly 16 minutes to 12 seconds in the [announcement](https://stryker-mutator.io/blog/stryker4s-v1/). That is a project result, not a universal promise.

[mutmut 3.7.0](https://pypi.org/project/mutmut/3.7.0/) now invalidates cached results when changes to a function, one of its callers, configuration, or relevant Git-tracked files could affect them. Changes to non-Python files trigger a warning by default and can force a full rerun.

| Tool | July update |
|---|---|
| [Stryker.NET 4.16.0](https://github.com/stryker-mutator/stryker-net/releases/tag/dotnet-stryker%404.16.0) | GitLab Code Quality reporter; preserved saved dashboard reports |
| PIT [1.25.6](https://github.com/hcoles/pitest/releases/tag/1.25.6), [1.25.7](https://github.com/hcoles/pitest/releases/tag/1.25.7), [1.25.8](https://github.com/hcoles/pitest/releases/tag/1.25.8) | Quarkus/JaCoCo handling, bytecode-library update, Java 25 big-number fixes |
| [Infection 0.34.1](https://github.com/infection/infection/releases/tag/0.34.1) | Git/PCOV path fixes and PHPStan memory-limit correction |
| [pytest-gremlins 1.9.0](https://github.com/mikelane/pytest-gremlins/releases/tag/v1.9.0) | `--gremlin-explain` plus coverage/config fixes; distributed July 1, changelog dated June 29 |
| [Gomutants 0.5.0](https://github.com/szhekpisov/gomutants/releases/tag/v0.5.0) | Call exclusions, forwarded test flags, exit codes, fixes |
| Mutago [2.7.6](https://github.com/quality-gates/mutago/releases/tag/v2.7.6)–[2.7.7](https://github.com/quality-gates/mutago/releases/tag/v2.7.7) | Corrected source and diff locations, filtered invalid Go mutants, and added pull-request runs limited to changed lines; overlapping [go-mutesting 2.7.9](https://github.com/jonbaldie/go-mutesting/releases/tag/v2.7.9) mainly directs users toward Mutago |
| [mutflow 1.0.4](https://github.com/anschnapp/mutflow/releases/tag/v1.0.4) | Avoided Kotlin null-check mutations; fixed double arithmetic types |
| [Pest Plugin Mutate 5.0.0](https://github.com/pestphp/pest-plugin-mutate/releases/tag/v5.0.0) | Pest 5/PHP 8.4 compatibility |
| [R muttest 0.3.0](https://cran.r-project.org/package=muttest) | Standard JSON/HTML reports for other tools to consume and clearer uncovered/crash states; errors now count as killed, so scores can move without stronger tests |
| [MutatoR 0.1.0–0.2.1](https://github.com/PRL-PRG/mutator/releases) | `tinytest`, coverage-guided selection, cleanup/Windows fixes |
| [Stryker Bun Runner](https://github.com/hughescr/stryker-bun-runner/releases) | Nine 1.3.x releases covering worker handling, source links from coverage results, debugger-race fixes, and incremental reports |
| [Mutation Testing Elements](https://github.com/stryker-mutator/mutation-testing-elements/releases) / [Stryker Dashboard 0.20.6](https://github.com/stryker-mutator/stryker-dashboard/releases/tag/v0.20.6) | Report rendering and terminal-color fixes, plus security and report validation |

Popular but quiet in July: StrykerJS, cargo-mutants, Mull, Cosmic Ray, Ruby Mutant, Major, and MuJava.

## New tools: mutation escaped the usual language zoo

“New” means the first verifiable public implementation or package appeared during July and no earlier public lineage was found. Most are v0.x projects, single-author efforts, only days old—or some combination of those traits.

| Tool | Target | Why it is interesting | Reality check |
|---|---|---|---|
| [Oasis](https://github.com/DegenerateUSER/Oasis) | Terraform/OpenTofu | Parses infrastructure configuration, applies domain-specific changes, runs `terraform test` | Began July 2; v0.0.4 |
| [PSMutant](https://github.com/Fortigi/PSMutant) | PowerShell/Pester | Syntax-tree mutation, only on lines reached by tests, in disposable sandboxes; [Gallery package](https://www.powershellgallery.com/packages/PSMutant/0.1.0) | One 0.1.0 release and six commits |
| [active_mutator](https://github.com/drj613/active_mutator) | Ruby/RSpec/Rails | Runs only test examples that cover the mutated code, isolates each run, and stores a list of mutants accepted as equivalent | [RubyGems 0.1.0](https://rubygems.org/gems/active_mutator/versions/0.1.0) to 0.3.0 in three weeks |
| [Cerebrum](https://github.com/islandhopper81/cerebrum) | Multiple languages | LLM-generated patches in isolated Git working directories, scoped by changed or test-covered code | Substantive [0.2.0 package](https://pypi.org/project/cerebrum-engine/0.2.0/); model and cloud dependent |
| [Stryker Zod mutator](https://github.com/enormora/schema-hub/tree/main/source/stryker-zod-mutator) | Zod v4 schemas | Forty schema-aware operators | Experimental 0.0.x; depends on undocumented internal Stryker APIs |

The experimental bench filled out quickly: [Angelo](https://github.com/kvandeh/Angelo) for Python/pytest, [codingconcepts/mutant](https://github.com/codingconcepts/mutant) for Go, [mutash](https://github.com/JaydenCJ/mutash) for Bash/Bats, [rexmut](https://github.com/JaydenCJ/rexmut) for regexes, [cobmut](https://github.com/acherm/cobol-mutator) for GnuCOBOL, [unimut](https://github.com/MyNameIsTrez/unimut) for C, and Microsoft's [mutate4csharp](https://github.com/microsoft/mutate4csharp), available as source code without a packaged release. Yes, COBOL got a new mutator too. Legacy remains very much alive.

No public source history was available for three code-bearing packages: [mutate4js](https://www.npmjs.com/package/mutate4js), [aegismut](https://www.npmjs.com/package/aegismut), and [`mutant-mcp`](https://www.npmjs.com/package/@ahmedshaikh/mutant-mcp). Package archives do not reveal development history.

Tools for AI agents and changed-code checks also appeared: [Playwright Mutation Gate](https://github.com/VladyslavDmitriiev/playwright-mutation-gate) reverses selected assertions, [MTT Harness](https://github.com/suleman-dawood/mtt-harness) runs scoped mutation for JavaScript, TypeScript, and Go, [AITG](https://github.com/tzurid25/aitg) limits Stryker to changed code, and [TestForge](https://github.com/vikaspad/TestForge) uses PIT to evaluate generated Java tests. Smaller experiments: [move-test-gen](https://github.com/mehvetero/move-test-gen), [DiffProof](https://github.com/muhzuhaib/diffproof), [Mutinerie](https://github.com/ranska/mutinerie), [Witness](https://github.com/sjgant80-hub/witness), [Quality Kit](https://github.com/tugkanpilka/quality-kit), [Quality Kit Python](https://github.com/tugkanpilka/quality-kit-python), [ToppleCat](https://github.com/samzhu/topplecat), [fineness](https://github.com/nicoseijas/fineness), [pytest-mutation-verified](https://github.com/nickharris808/pytest-mutation-verified), and [falsify](https://github.com/KrystianMarek/falsify).

Excluded after lineage checks: [Gutcheck](https://github.com/beepometer/gutcheck), [Chaos-MCP](https://github.com/AraneaDev/Chaos-MCP), [Flawd](https://github.com/fixture-dev/flawd-releases), and [QuMu](https://www.scitepress.org/publishedPapers/2025/135610/pdf/index.html).


## Patent watch: one grant, no new disclosure

China granted Beihang University's [CN115712574B](https://patents.google.com/patent/CN115712574B/en) on July 7; the [China National Intellectual Property Administration](https://pss-system.cponline.cnipa.gov.cn/) is the official lookup. Its AI-component pipeline uses killed-mutant ratios across data, model, and program mutants. But the same language appeared in [CN115712574A](https://patents.google.com/patent/CN115712574A/en) in February 2023: July brought a grant, not a new mutation engine. A search of July patent claims found no second relevant item; indexing may lag, and this is not legal advice.

## Further reading and listening

- Trail of Bits' [DAML smart-contract-language case study](https://blog.trailofbits.com/2026/07/08/mutation-testing-comes-to-daml/) triaged seven survivors into three equivalent/unreachable cases and four missing tests; code shipped in June, and no bug was claimed.
- NEXL's [Ruby field report](https://medium.com/nexl-engineering/how-mutation-testing-keeps-my-ai-agents-code-honest-8676b8605bcc) attributes two stack-overflow crashes to survivor-driven work despite full coverage; no raw report is linked.
- H. Floyd's [“Your Tests Pass. So What?”](https://durabilitycurve.com/blog/your-tests-pass-so-what/) reports 38 of 105 mutants killed, then turns 67 survivors into a Claude work queue—with good warnings about equivalent mutants and locking existing wrong behavior into new tests.
- Mariano Álvarez's [StrykerJS field note](https://marianoalvarez.dev/blog/mutando-codigo-con-strykerjs/) puts more than 90% coverage beside a 71.42% mutation score; the private codebase prevents reproduction.
- [Ruby on Rails Podcast #541](https://www.therubyonrailspodcast.com/541) interviews Mutant author Markus Schirp about mutation testing in agent-written code.
- Pierre Donat-Bouillud's [useR! MutatoR talk](https://events.digital-research.academy/event/109/contributions/483/) and Jakub Sobolewski's [muttest 0.3.0 post](https://jakubsobolewski.com/blog/muttest-0_3_0/) cover R tooling and interoperable reports.
- Vladyslav Dmitriiev's [Playwright post](https://dmitriiev.dev/posts/your-green-tests-are-lying/) and [Reddit discussion](https://www.reddit.com/r/softwaretesting/comments/1uy48hv/mutation_testing_for_e2e_mutate_the_assertion_not/) report that tests caught 63 deliberately reversed assertions while one skipped test could not be scored. This still does not prove that tests check the application's real external behavior.

No high-signal July-first Hacker News or Lobsters story surfaced.

## What July reinforced

1. **Several projects turned survivors into actionable inputs:** one exact change, one test-writing or review task—not another dashboard percentage.
2. **July's audits reinforced that behavioral witnesses beat impressive proxies:** training-data leakage, poorly separated evaluation data, manually editable baselines, and truncated output can flatter a score.
3. **Repository experience again showed that adoption depends on domain fit and total cost:** compute, model calls, caching, dependencies, flaky runs, accumulated unresolved survivors, and review time all count.

Progress, not magic. Magic has terrible reproducibility.
