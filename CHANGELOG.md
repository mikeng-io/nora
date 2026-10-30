# Changelog

## [7.11.53](https://github.com/mikeng-io/nora/compare/v7.11.52...v7.11.53) (2026-10-30)


### Bug Fixes

* **replay:** restore recorded session clocks and typed subject overrides ([e7dcd15](https://github.com/mikeng-io/nora/commit/e7dcd157b03d3150cbca337f7ce03b91b97244c9))

## [7.11.52](https://github.com/mikeng-io/nora/compare/v7.11.51...v7.11.52) (2026-10-30)


### Maintenance

* **memory:** bound Honcho cutover acceptance to available evidence ([242e295](https://github.com/mikeng-io/nora/commit/242e29598fc1e30ef7f0ecf58c8cad97b4473670))
* **memory:** qualify remaining historical cutover formulas ([8d32ab7](https://github.com/mikeng-io/nora/commit/8d32ab7ac2a41338b07d8ae562bc94025c31623e))

## [7.11.51](https://github.com/mikeng-io/nora/compare/v7.11.50...v7.11.51) (2026-10-30)


### Maintenance

* **observability:** align dependent design bundles with SigNoz ([590e5b1](https://github.com/mikeng-io/nora/commit/590e5b1fd7e806e62b262a8daf21aa1eab6bad72))
* **observability:** record the SigNoz migration design ([396c7a6](https://github.com/mikeng-io/nora/commit/396c7a68ed317fca400946de5e1373f0e28c5cd9))

## [7.11.50](https://github.com/mikeng-io/nora/compare/v7.11.49...v7.11.50) (2026-10-30)


### Features

* **memory:** measure recorded Cognition sessions in the snapshot spike ([f1648d8](https://github.com/mikeng-io/nora/commit/f1648d80ad5adc6c0561e585536f7783bd4d3073))


### Bug Fixes

* **memory:** close snapshot spike review measurement gaps ([2c4f5cb](https://github.com/mikeng-io/nora/commit/2c4f5cb33e1abf9a0bfd099f381a7c9355d24135))

## [7.11.49](https://github.com/mikeng-io/nora/compare/v7.11.48...v7.11.49) (2026-10-30)


### Maintenance

* **memory:** preserve graph findings and platform observation design ([c156fea](https://github.com/mikeng-io/nora/commit/c156feada35e8f74aacf227234fbe30cbed7f230))
* **memory:** reference audited source by verified symbols ([9dddd00](https://github.com/mikeng-io/nora/commit/9dddd0039967e92f7bb4ca395a5d695f107722ec))
* **platforms:** expand observation contracts into a design bundle ([33748b6](https://github.com/mikeng-io/nora/commit/33748b64458df4928e144068696bcfae6274db8f))
* **platforms:** give observation acceptance one owner ([a2b8c22](https://github.com/mikeng-io/nora/commit/a2b8c22f46bf17804f49c6ab6ebce494f30f0489))

## [7.11.48](https://github.com/mikeng-io/nora/compare/v7.11.47...v7.11.48) (2026-10-30)


### Bug Fixes

* **cognition:** keep reading unclaimed ids written as a string or null ([c92816a](https://github.com/mikeng-io/nora/commit/c92816ac37ce3346cdbd420c30b993c4ac33a6ee))
* **cognition:** read a recorded session agenda written as a string ([c76c264](https://github.com/mikeng-io/nora/commit/c76c2646d0f3ea8a3860a57121f3363c429aa22b))
* **cognition:** replay a wake whose detail was stored as a string ([64eba21](https://github.com/mikeng-io/nora/commit/64eba21842c747f8f7367a329bb3a429489aa8b6))

## [7.11.47](https://github.com/mikeng-io/nora/compare/v7.11.46...v7.11.47) (2026-10-30)


### Features

* **evals:** anchor cognition scenarios on recorded wakes ([fe4c7c7](https://github.com/mikeng-io/nora/commit/fe4c7c7507436fec49d1fd14d7946af1fd711b22))
* **evals:** anchor scenarios on recorded wakes, in progress ([5efb8d8](https://github.com/mikeng-io/nora/commit/5efb8d89b2f771fa6d730565dc1b9c99731c20f2))


### Bug Fixes

* **evals:** an inspection returns the render before any session row is written ([7504b9e](https://github.com/mikeng-io/nora/commit/7504b9eeebc6cb66e8a708a17f8f8e0ce66cbd09))
* **evals:** the anchor confirmation's prompt is a declared context-assembly occasion ([5f77b91](https://github.com/mikeng-io/nora/commit/5f77b911c7646e879b202d75148b4c8f71e53984))
* **evals:** the confirmation's memory read reduces profile lines to chain heads first ([06afdb0](https://github.com/mikeng-io/nora/commit/06afdb0cfba23aab8d07bdfb4c5ac7e1be624aac))
* **evals:** the context selector's prompt is a declared context-assembly occasion ([bfbdf03](https://github.com/mikeng-io/nora/commit/bfbdf030355c12a2cb7ff990d4bd2397ed5d5208))

## [7.11.46](https://github.com/mikeng-io/nora/compare/v7.11.45...v7.11.46) (2026-10-30)


### Maintenance

* **bridges:** clarify receive preflight and reader election scope ([b0ea7ff](https://github.com/mikeng-io/nora/commit/b0ea7fff57a7e0da962386e9ecf240ccb2acbe7f))
* **bridges:** resolve ingress and recovery review gaps ([3740cb8](https://github.com/mikeng-io/nora/commit/3740cb85624fac81795c4a03f0a55c730d195050))
* **bridges:** specify durable platform handoff across app releases ([c8824d8](https://github.com/mikeng-io/nora/commit/c8824d8c48e67ae467677d820340660d22e5bff8))

## [7.11.45](https://github.com/mikeng-io/nora/compare/v7.11.44...v7.11.45) (2026-10-30)


### Maintenance

* **memory:** use one vocabulary for registered notebook tools ([2da875d](https://github.com/mikeng-io/nora/commit/2da875d1bfed16403682fd0988ec91cee2986856))
* restore the note retirements metric name ([7855ed3](https://github.com/mikeng-io/nora/commit/7855ed317f8d288d38aa8bce0a02ddf563109b48))

## [7.11.44](https://github.com/mikeng-io/nora/compare/v7.11.43...v7.11.44) (2026-10-30)


### Bug Fixes

* **cognition:** close the review's gaps in the one session ([27b1bef](https://github.com/mikeng-io/nora/commit/27b1bef6387e11dbde96b32069ffdd4e2937e8f4))
* **cognition:** include message-owned evidence in wake comprehension ([377d51d](https://github.com/mikeng-io/nora/commit/377d51d134ad459ebaebee7e32f3fd912f9ff456))
* **cognition:** preserve the disabled surface and expose operator requeue ([8d2fb29](https://github.com/mikeng-io/nora/commit/8d2fb29808e9a28cdb0b9571a53b63b52cc77a13))
* **cognition:** replay social batches and relink committed Register rows ([27396cb](https://github.com/mikeng-io/nora/commit/27396cb201f41bfb2bc54922592867641b143134))
* **cognition:** respect the eval boundary and return local admission refusals ([660119f](https://github.com/mikeng-io/nora/commit/660119fc0cec3f3d535d3279dbfe8d3e8bed8fdf))
* **cognition:** run a wake whose message is not projected yet ([a22b628](https://github.com/mikeng-io/nora/commit/a22b6287c3b40126702130fcf3d02e5b3df81b23))

## [7.11.43](https://github.com/mikeng-io/nora/compare/v7.11.42...v7.11.43) (2026-10-30)


### Bug Fixes

* **evals:** dial named model providers on loopback from the host ([7f142af](https://github.com/mikeng-io/nora/commit/7f142afb0861dfc1d31d0615b197b6fe8536da96))
* **evals:** give the judge's endpoint the same host rewrite ([1be6393](https://github.com/mikeng-io/nora/commit/1be63932ccc0ed068e684e88b9ea212ffe086b73))

## [7.11.42](https://github.com/mikeng-io/nora/compare/v7.11.41...v7.11.42) (2026-10-30)


### Features

* **memory:** return scoped subgraphs from memory search and read ([d2c58e6](https://github.com/mikeng-io/nora/commit/d2c58e647f1236f5d88489a26253845cb1ff2438))


### Bug Fixes

* **memory:** declare memory_search's person statuses and note the narrowing gap ([87afd17](https://github.com/mikeng-io/nora/commit/87afd173cdc48301c4ec167d3977181ee88be258))
* **memory:** let a narrowed memory_search walk again in the same turn ([b93e1b1](https://github.com/mikeng-io/nora/commit/b93e1b1c9ee9029a38fc86ecc51eca06ad55e9af))
* **tools:** list memory_search's statuses as before ([dca8dd7](https://github.com/mikeng-io/nora/commit/dca8dd7e616731ce0dbd0166926a2d6dc9ec14bb))
* **tools:** restore memory_search's argument guidance ([17d5e20](https://github.com/mikeng-io/nora/commit/17d5e20dba599fc78e35e7c370df66370a7a70b4))

## [7.11.41](https://github.com/mikeng-io/nora/compare/v7.11.40...v7.11.41) (2026-10-30)


### Bug Fixes

* **ops:** keep host commands usable after secret migration ([f3c8eb2](https://github.com/mikeng-io/nora/commit/f3c8eb2b969ad97285950fc3fe2daaafce0e65be))

## [7.11.40](https://github.com/mikeng-io/nora/compare/v7.11.39...v7.11.40) (2026-10-30)


### Bug Fixes

* **config:** resolve secrets for host Compose audit sync ([c80dcdd](https://github.com/mikeng-io/nora/commit/c80dcdd046a2e3476a681dfbc4e017a715842a30))

## [7.11.39](https://github.com/mikeng-io/nora/compare/v7.11.38...v7.11.39) (2026-10-30)


### Bug Fixes

* **deploy:** write secret projections in the writable control directory ([1eb32c6](https://github.com/mikeng-io/nora/commit/1eb32c6e695398bd13da738c3dca78faaa2e49a0))
* **secrets:** exclude generated delivery files at every depth ([1415cc9](https://github.com/mikeng-io/nora/commit/1415cc9683a80e08687ba5a140b7bb4b662e2167))

## [7.11.38](https://github.com/mikeng-io/nora/compare/v7.11.37...v7.11.38) (2026-10-30)


### Bug Fixes

* **config:** complete judge routing and degrade missing media credentials ([3d26c18](https://github.com/mikeng-io/nora/commit/3d26c1842d73ecdd900a793ad7a17ae64ffe7cb0))
* **config:** limit missing-credential warnings to provider names ([30c2a52](https://github.com/mikeng-io/nora/commit/30c2a52ee290b2ba0cd0e174274c20d074912d66))

## [7.11.37](https://github.com/mikeng-io/nora/compare/v7.11.36...v7.11.37) (2026-10-29)


### Bug Fixes

* **evals:** cover Cantonese anchor forms and carry each rule's strength ([f25a082](https://github.com/mikeng-io/nora/commit/f25a082e75f9fd6c65122031a00f4228ac70089b))
* **evals:** select plausible cognition anchor candidates ([a65636d](https://github.com/mikeng-io/nora/commit/a65636de5c5e6e92b6f464249a6000f027db8470))

## [7.11.36](https://github.com/mikeng-io/nora/compare/v7.11.35...v7.11.36) (2026-10-29)


### Bug Fixes

* **evals:** name the confirmer's answer form instead of a response format ([377206c](https://github.com/mikeng-io/nora/commit/377206ce709cbcc48bf937ece1e894fad9b4b39c))

## [7.11.35](https://github.com/mikeng-io/nora/compare/v7.11.34...v7.11.35) (2026-10-29)


### Features

* **evals:** confirm cognition anchors with the deployment model ([f294210](https://github.com/mikeng-io/nora/commit/f294210a42ca9c33f5e167e743205ffacb75d71d))


### Bug Fixes

* **evals:** bound the confirmer's window and pin its scenario table ([3d133be](https://github.com/mikeng-io/nora/commit/3d133bef7c38f1a9a4d834e9259145a880221e1c))

## [7.11.34](https://github.com/mikeng-io/nora/compare/v7.11.33...v7.11.34) (2026-10-29)


### Bug Fixes

* **issues:** recognize the documented Note tier area ([2920fd1](https://github.com/mikeng-io/nora/commit/2920fd1312e276d7d93bf4b8e1866643005f817c))

## [7.11.33](https://github.com/mikeng-io/nora/compare/v7.11.32...v7.11.33) (2026-10-29)


### Maintenance

* **portal:** await download persistence before cleanup ([452fccf](https://github.com/mikeng-io/nora/commit/452fccf6e76d4e2dd25bdbc31889e719ccc37270))
* bind security findings to the analyzed merge and isolate baseline runs ([784944e](https://github.com/mikeng-io/nora/commit/784944e142c56ec1d7735f0a89f23641edcc84f2))
* resolve query-pack rules in CodeQL SARIF ([33aa092](https://github.com/mikeng-io/nora/commit/33aa0922a2de39f7dd4d1e46f35b5a81a8c620c0))
* select PR checks by changed inputs and consolidate workflows ([5e169a1](https://github.com/mikeng-io/nora/commit/5e169a16b07db4587dbbb663603c7cdbd4bfd0b8))

## [7.11.32](https://github.com/mikeng-io/nora/compare/v7.11.31...v7.11.32) (2026-10-29)


### Maintenance

* **cognition:** add the parked one-session scenario suite ([ce23f74](https://github.com/mikeng-io/nora/commit/ce23f749f11836e008bc9f4c8908a3424e46aafb))
* **cognition:** bind non-empty readings and widen the withheld acts ([595a6f7](https://github.com/mikeng-io/nora/commit/595a6f7554e4da78cad89eda017226e0f232a610))

## [7.11.31](https://github.com/mikeng-io/nora/compare/v7.11.30...v7.11.31) (2026-10-29)


### Maintenance

* **issues:** mark incomplete reports and clear feedback on correction ([a0850e0](https://github.com/mikeng-io/nora/commit/a0850e05b1c0c1735c2d30231ee739ee7c948d1c))

## [7.11.30](https://github.com/mikeng-io/nora/compare/v7.11.29...v7.11.30) (2026-10-29)


### Maintenance

* **cognition:** brief the one-session build, scenarios first ([cb4b99b](https://github.com/mikeng-io/nora/commit/cb4b99b5ed7bf4ffb37aa60ca7806fd1bace037c))
* **cognition:** move the harness with the runner, require consumption tests ([762d3cd](https://github.com/mikeng-io/nora/commit/762d3cd07d4f30501645e77c3c3867cc84d8d69c))

## [7.11.29](https://github.com/mikeng-io/nora/compare/v7.11.28...v7.11.29) (2026-10-29)


### Security

* **deps:** bump the npm-security group across 4 directories with 3 updates ([b03c4ae](https://github.com/mikeng-io/nora/commit/b03c4ae4232b42a69cb11a38889d3f44956b483c))

## [7.11.28](https://github.com/mikeng-io/nora/compare/v7.11.27...v7.11.28) (2026-10-29)


### Maintenance

* **cognition:** correct the work-item coverage and carry retries to the wake ([a229097](https://github.com/mikeng-io/nora/commit/a229097a1e8a445878287eff7f689090b5d8efd1))
* **cognition:** one session comprehends, connects and decides ([c2315c2](https://github.com/mikeng-io/nora/commit/c2315c2587a8d7958c2eaa8e6abac66565c2eccd))

## [7.11.27](https://github.com/mikeng-io/nora/compare/v7.11.26...v7.11.27) (2026-10-29)


### Bug Fixes

* **memory:** resolve step one's person ends and refuse untargeted evidence ([9be5d26](https://github.com/mikeng-io/nora/commit/9be5d26f40df9eed2268fcc78d548fa080d8681b))
* **spike:** mark productive writes, each smoke mark on its own ([aa936d8](https://github.com/mikeng-io/nora/commit/aa936d8ac70b0cda4bd34af591b1f7bb7a5cea81))

## [7.11.26](https://github.com/mikeng-io/nora/compare/v7.11.25...v7.11.26) (2026-10-29)


### Bug Fixes

* **memory:** echo only schema field names and error types on a refusal ([f59f691](https://github.com/mikeng-io/nora/commit/f59f6917bb5548c79e15b3a373c3e1f3f92f72c1))
* **memory:** tell step one where its write was the wrong shape ([50ce3f2](https://github.com/mikeng-io/nora/commit/50ce3f2d7bab6de5fb02aab11b9de42bae810d53))

## [7.11.25](https://github.com/mikeng-io/nora/compare/v7.11.24...v7.11.25) (2026-10-29)


### Features

* **cognition:** measure topic activity and stage shadow accounts ([88c1cbf](https://github.com/mikeng-io/nora/commit/88c1cbf3bc7984e4517439eba93a6e4c450fd9d8))
* **notebook:** add shared entry revisions with topic rules ([ebe17c9](https://github.com/mikeng-io/nora/commit/ebe17c9ae350b69217920ff4e9b24330ef9448bd))

## [7.11.24](https://github.com/mikeng-io/nora/compare/v7.11.23...v7.11.24) (2026-10-29)


### Maintenance

* **memory:** correct the PROV mapping on entity, polarity and time ([2164741](https://github.com/mikeng-io/nora/commit/216474151f63c626c304b0999fe9973b5ddf8296))
* **memory:** reference the graph's provenance to W3C PROV ([59c1ca6](https://github.com/mikeng-io/nora/commit/59c1ca603088e6d56ab635d56ac5f34acf5d49d4))

## [7.11.23](https://github.com/mikeng-io/nora/compare/v7.11.22...v7.11.23) (2026-10-29)


### Bug Fixes

* **memory:** offer step one the about relation from what it read ([3e02f32](https://github.com/mikeng-io/nora/commit/3e02f326172ed03a217fe19f204c7547f1b13cfb))
* **memory:** show step one the graph vocabulary, and add topics ([d26d690](https://github.com/mikeng-io/nora/commit/d26d690836ab18bfaa7ba52008b6c9870676e84b))

## [7.11.22](https://github.com/mikeng-io/nora/compare/v7.11.21...v7.11.22) (2026-10-29)


### Maintenance

* **notebook:** amend turn records for the decision split ([94811be](https://github.com/mikeng-io/nora/commit/94811bebc1173ecb4bd65d557b47872ccceaf3be))
* **notebook:** organise the Notebook by memory type, not tiers ([3fcd9ee](https://github.com/mikeng-io/nora/commit/3fcd9ee8180c49993abea675a3f8f913246715de))
* **notebook:** record how the memory types relate to the profile ([f8b9ebd](https://github.com/mikeng-io/nora/commit/f8b9ebddd1d604ef8410e25c3a492e6f01f55291))

## [7.11.21](https://github.com/mikeng-io/nora/compare/v7.11.20...v7.11.21) (2026-10-29)


### Bug Fixes

* **memory:** put items with a media source in the spike's attachment stratum ([4457126](https://github.com/mikeng-io/nora/commit/44571262a9eb25e9f10627512e84587a90337c28))
* **memory:** score spike readings from the item's tuple-shaped sources ([b9cf618](https://github.com/mikeng-io/nora/commit/b9cf6181d8e0c33c57dba8168022e311e9cd6250))

## [7.11.20](https://github.com/mikeng-io/nora/compare/v7.11.19...v7.11.20) (2026-10-29)


### Bug Fixes

* **memory:** build the spike's search backend the way production does ([3dd12d5](https://github.com/mikeng-io/nora/commit/3dd12d5cf53422d1e02917457b21474c7cc91f81))

## [7.11.19](https://github.com/mikeng-io/nora/compare/v7.11.18...v7.11.19) (2026-10-29)


### Bug Fixes

* **memory:** count render budgets without the dev-only tiktoken ([83fb83e](https://github.com/mikeng-io/nora/commit/83fb83e8abead652ecf355e2f28c14f07f73e4c1))
* **test:** judge only runtime imports with a local provider ([5478426](https://github.com/mikeng-io/nora/commit/5478426c17a04add01afffa488711a83fba76950))

## [7.11.18](https://github.com/mikeng-io/nora/compare/v7.11.17...v7.11.18) (2026-10-29)


### Features

* **memory:** add an operator snapshot graph spike ([ed80a47](https://github.com/mikeng-io/nora/commit/ed80a47bf633f4ec91c59c7d44ca8a88bc737175))


### Bug Fixes

* **memory:** count spike sessions per work item and report token cost ([d86907d](https://github.com/mikeng-io/nora/commit/d86907d7d2d6b4fcd5d4092bf17fbd59acf48278))
* **memory:** refuse snapshot database routing overrides ([4d8374a](https://github.com/mikeng-io/nora/commit/4d8374a6f088a7c1ee8bce83af7bc091b6fba27d))

## [7.11.17](https://github.com/mikeng-io/nora/compare/v7.11.16...v7.11.17) (2026-10-29)


### Bug Fixes

* **domain:** a set minus a LiveSet yields a working set, not a spent generator ([00bfc3a](https://github.com/mikeng-io/nora/commit/00bfc3abd7c3be281c5b65baf55823f0a7a61adc))
* **domain:** type _from_iterable over its own TypeVar per the AbstractSet contract ([bbe6e2f](https://github.com/mikeng-io/nora/commit/bbe6e2f0f669ba51ada113a76fd5a1908b4981ff))

## [7.11.16](https://github.com/mikeng-io/nora/compare/v7.11.15...v7.11.16) (2026-10-28)


### Features

* **discord:** admit a whole server by guild id, with threads as tags in their channel's room ([87ce614](https://github.com/mikeng-io/nora/commit/87ce6142d19e4b6da93a0bba40d2ad33c374d3c1))


### Bug Fixes

* **discord:** enumerate rooms() in every room consumer, fail the absorb read closed on a mid-turn vanish ([50b9cf6](https://github.com/mikeng-io/nora/commit/50b9cf6de6e75e2539e492d9868f1d375ec853e6))

## [7.11.15](https://github.com/mikeng-io/nora/compare/v7.11.14...v7.11.15) (2026-10-28)


### Features

* **memory:** comprehend graph arrivals and reshape profiles in shadow ([903bee2](https://github.com/mikeng-io/nora/commit/903bee2424c48a2404c38b5c33e5a8bfaf576b55))


### Bug Fixes

* **memory:** consume graph arrivals and retry Register link projections ([7c553a0](https://github.com/mikeng-io/nora/commit/7c553a05a76e743598165930cef1511b57995354))
* **memory:** reopen Register links and isolate shadow proposal refusals ([6123e4e](https://github.com/mikeng-io/nora/commit/6123e4eed24aaf8210685fdb648b7f5bc446807f))

## [7.11.14](https://github.com/mikeng-io/nora/compare/v7.11.13...v7.11.14) (2026-10-28)


### Maintenance

* **tooling:** connect project agents to Blender MCP ([261bc0f](https://github.com/mikeng-io/nora/commit/261bc0fe9f0042bb5adb6ba9b4ebebbca27f708b))

## [7.11.13](https://github.com/mikeng-io/nora/compare/v7.11.12...v7.11.13) (2026-10-28)


### Features

* **memory:** persist the scoped graph and queue cognition inputs ([1ba8d3e](https://github.com/mikeng-io/nora/commit/1ba8d3e25ffec0833fe353f65e2b47273bd6c1fe))


### Bug Fixes

* **memory:** follow live identity and room policy in graph projection ([c172209](https://github.com/mikeng-io/nora/commit/c17220947bce802c839c613f18c0dfdcd6d7edc4))
* **memory:** preserve graph projection exclusions and prove hub bounds ([b6c122b](https://github.com/mikeng-io/nora/commit/b6c122ba5f0ea939a0064862c5680ece632da3f4))

## [7.11.12](https://github.com/mikeng-io/nora/compare/v7.11.11...v7.11.12) (2026-10-28)


### Features

* **policy:** hot-reload validated identity and room policy ([61459ac](https://github.com/mikeng-io/nora/commit/61459aca908103620dbbe32571b7b479efc2fcc1))


### Bug Fixes

* **culture:** resolve room policy lookups live, not at boot ([d3040ee](https://github.com/mikeng-io/nora/commit/d3040ee75eca3bcafaec3145a1c763ae6c811d66))
* **policy:** retain declared workspace routes when the last room leaves ([d360cfb](https://github.com/mikeng-io/nora/commit/d360cfb32697f53e17b6f98fc62e4ef00ad2a638))

## [7.11.11](https://github.com/mikeng-io/nora/compare/v7.11.10...v7.11.11) (2026-10-28)


### Maintenance

* **memory:** let curation read the private scope and keep ledger links in the notebook layer ([4b6078c](https://github.com/mikeng-io/nora/commit/4b6078c79f9f9b8d4f07276df64e67d37ef847bf))
* **memory:** settle where the graph meets the DM, ledger and personalisation bundles ([2d2958b](https://github.com/mikeng-io/nora/commit/2d2958be81a179893647da194d00867e2c821e20))

## [7.11.10](https://github.com/mikeng-io/nora/compare/v7.11.9...v7.11.10) (2026-10-28)


### Maintenance

* **mcp:** add project Sentry OAuth connection ([32c4323](https://github.com/mikeng-io/nora/commit/32c4323db0391301d2fcd8b1f5ddfe09ec38655a))
* Merge branch 'master' into config/sentry-project-mcp ([3fd0172](https://github.com/mikeng-io/nora/commit/3fd0172c55fa5334c7b82b5387a9876bddb0263d))
* Merge pull request #332 from mikeng-io/config/sentry-project-mcp ([e60d2a6](https://github.com/mikeng-io/nora/commit/e60d2a6222f43c7badb3f86970a363cef8deb869))

## [7.11.9](https://github.com/mikeng-io/nora/compare/v7.11.8...v7.11.9) (2026-10-28)


### Bug Fixes

* **memory:** key identity pointers per unit and fix an entity's unit ([069b986](https://github.com/mikeng-io/nora/commit/069b986fff693247233f3e049a57f6a73c497fcf))
* **memory:** make pointer ids unique across kinds and pin cross-unit same_as ([8b85661](https://github.com/mikeng-io/nora/commit/8b856617e2bf8ab204623d99b1c1991753dd938d))

## [7.11.8](https://github.com/mikeng-io/nora/compare/v7.11.7...v7.11.8) (2026-10-28)


### Features

* **memory:** define the shared graph storage and admission contract ([c962d5c](https://github.com/mikeng-io/nora/commit/c962d5c95d695ca9a8707947f44692d2337eab95))


### Bug Fixes

* **memory:** add relation corrections and entity lookup to the graph contract ([1695dd1](https://github.com/mikeng-io/nora/commit/1695dd1466fe1d8616804020fe0e2ecdaa59196c))
* **memory:** require asserted_at, accumulate attributes, name the batch arguments ([61a6434](https://github.com/mikeng-io/nora/commit/61a64348bfeead0766df568ea20e3992bd57c737))

## [7.11.7](https://github.com/mikeng-io/nora/compare/v7.11.6...v7.11.7) (2026-10-28)


### Maintenance

* **cognition:** describe the one loop in the cognition bundle itself ([f9550ea](https://github.com/mikeng-io/nora/commit/f9550ea52a24b2c5a33b8d0af6845fd818f1620b))
* **cognition:** fix the frontmatter and point every old-topology section at the one loop ([e027329](https://github.com/mikeng-io/nora/commit/e0273299a3533595d05ec73018f3b943744740b3))

## [7.11.6](https://github.com/mikeng-io/nora/compare/v7.11.5...v7.11.6) (2026-10-28)


### Maintenance

* **memory:** push the graph preview into comprehension ([8de3ec5](https://github.com/mikeng-io/nora/commit/8de3ec598608fe6bb8c1d3eadf9ecc43e1ac8675))

## [7.11.5](https://github.com/mikeng-io/nora/compare/v7.11.4...v7.11.5) (2026-10-28)


### Maintenance

* **memory:** comprehend on arrival and fold culture and lessons into Cognition ([54aecaf](https://github.com/mikeng-io/nora/commit/54aecaf7a57de00e75711e73851cd6a6fb9377b3))
* **memory:** correct the culture readers and wake reasons in node 27 ([518b412](https://github.com/mikeng-io/nora/commit/518b412e9bcf56894110826b379d4516da292583))

## [7.11.4](https://github.com/mikeng-io/nora/compare/v7.11.3...v7.11.4) (2026-10-28)


### Maintenance

* **memory:** apply review fixes to the memory graph node ([3d13a0f](https://github.com/mikeng-io/nora/commit/3d13a0fe4191711c4a0f2f837d5b1d6b3562305c))
* **memory:** make Cognition the one loop that comprehends, reshapes and acts ([e98298c](https://github.com/mikeng-io/nora/commit/e98298c543349dc0fedda83af9010adf0099ab90))
* **memory:** make the memory a graph with a research loop ([e67e9d5](https://github.com/mikeng-io/nora/commit/e67e9d5db4bc5bfa5b112b7eac4f3083f85777bf))
* **memory:** place the memory loop before Cognition and give each item one writer ([92b517b](https://github.com/mikeng-io/nora/commit/92b517bc9afc0a9f5a26072db2b5a88bf80276d8))
* **memory:** ratify the memory graph and name who writes each relation ([5cdf6c1](https://github.com/mikeng-io/nora/commit/5cdf6c19f96877fd07bd1c4175fb7807299f4452))
* **memory:** ship step 2 before the backfill and measure its cost ([c998b86](https://github.com/mikeng-io/nora/commit/c998b86a126a04569c536d817f1a2629e53b529d))

## [7.11.3](https://github.com/mikeng-io/nora/compare/v7.11.2...v7.11.3) (2026-10-28)


### Bug Fixes

* **profile:** keep register relevance reads selective ([2ae6032](https://github.com/mikeng-io/nora/commit/2ae603257dae3ed0d7943e21e0969958fd36926c))
* **profile:** reuse scoped projections in source checks ([8d99c5c](https://github.com/mikeng-io/nora/commit/8d99c5cb022a3b2ad1cc26e8d3208bfd814d22ad))

## [7.11.2](https://github.com/mikeng-io/nora/compare/v7.11.1...v7.11.2) (2026-10-28)


### Bug Fixes

* **deploy:** preserve unchanged release artifacts and containers ([71b0a59](https://github.com/mikeng-io/nora/commit/71b0a5949f713257a8201162a4338802f196a135))
* **deploy:** verify retained containers against their restart baseline ([f0bd019](https://github.com/mikeng-io/nora/commit/f0bd01961c75f6eb7b19aa53d18ae0f9c63145d7))

## [7.11.1](https://github.com/mikeng-io/nora/compare/v7.11.0...v7.11.1) (2026-10-28)


### Bug Fixes

* **record:** align extraction schema checks with drop counts ([08eb580](https://github.com/mikeng-io/nora/commit/08eb580cfaf57250b97aa2062a6d34427dc58bbd))
* **record:** publish valid extraction claims and record provenance drops ([5e5cb5c](https://github.com/mikeng-io/nora/commit/5e5cb5c12de49c2884b7958090d12b9ddc2b0516))
* **release:** describe internal-only releases in their notes ([9a1c646](https://github.com/mikeng-io/nora/commit/9a1c64686fd7056b06695b619b7a6bf22abd696a))


### Changes

* **policy:** a unit may hold two rooms on one platform ([02a0148](https://github.com/mikeng-io/nora/commit/02a014801e7a28e96532e9d50ebe00b15fec9587))

## [7.11.0](https://github.com/mikeng-io/nora/compare/v7.10.0...v7.11.0) (2026-10-27)


### Features

* **record:** backfill historical extraction without delaying live messages ([e3c54f2](https://github.com/mikeng-io/nora/commit/e3c54f2af10e800f9327ec3731347b7e5127c704))


### Bug Fixes

* **record:** retain historical Nora context and commit backfill in chunks ([2662a5d](https://github.com/mikeng-io/nora/commit/2662a5d1a05acc6d39e701b29817f1604488b3d1))

## [7.10.0](https://github.com/mikeng-io/nora/compare/v7.9.0...v7.10.0) (2026-10-27)


### Features

* **app:** archive the downloads library as one zip ([a16f734](https://github.com/mikeng-io/nora/commit/a16f734cdcafb851434dcddb29deaf4dff874bce))


### Bug Fixes

* **app:** name downloads after the media, not download.<ext> ([9a78514](https://github.com/mikeng-io/nora/commit/9a78514375e13bacc8d3adc1d93a972134c1159f))

## [7.9.0](https://github.com/mikeng-io/nora/compare/v7.8.1...v7.9.0) (2026-10-27)


### Features

* **portal:** a More sheet on the phone's bottom bar reaches every rail destination ([1b1c287](https://github.com/mikeng-io/nora/commit/1b1c2872a30b059ad0e778b30857a1c81f2d12ba))


### Bug Fixes

* **portal:** reserve the scrollbar gutter so scroll-lock no longer pops the page ([2086a1d](https://github.com/mikeng-io/nora/commit/2086a1df53e69b5082a9ae3f05cae012a97864c1))

## [7.8.1](https://github.com/mikeng-io/nora/compare/v7.8.0...v7.8.1) (2026-10-27)


### Bug Fixes

* **app:** name a converted download after its own file ([61d108a](https://github.com/mikeng-io/nora/commit/61d108a64937d01a78d6f5b43dfe19555b025f4a))

## [7.8.0](https://github.com/mikeng-io/nora/compare/v7.7.0...v7.8.0) (2026-10-27)


### Features

* **app:** let a downloaded video convert to an audio format ([34ef871](https://github.com/mikeng-io/nora/commit/34ef87180bb16f52b13902dbf1f620f6175bcf4b))

## [7.7.0](https://github.com/mikeng-io/nora/compare/v7.6.0...v7.7.0) (2026-10-27)


### Features

* **relay:** address send_to by room within the unit ([09bf838](https://github.com/mikeng-io/nora/commit/09bf838b2ec291848926e28bdbc25c5a15df9fc6))

## [7.6.0](https://github.com/mikeng-io/nora/compare/v7.5.0...v7.6.0) (2026-10-27)


### Features

* **record:** ground reply claims and bound extraction retries ([d39ce44](https://github.com/mikeng-io/nora/commit/d39ce4434409b51fc0b3702cb77dd70d6db3d508))


### Bug Fixes

* **record:** accept extraction spans verbatim or decoded ([4c42314](https://github.com/mikeng-io/nora/commit/4c4231432c701441446ab56c05737715a344e2d6))
* **record:** align extraction references and escaped source spans ([8df302c](https://github.com/mikeng-io/nora/commit/8df302ce03b5995a88b4d14f370ffa1b5e4e8722))

## [7.5.0](https://github.com/mikeng-io/nora/compare/v7.4.0...v7.5.0) (2026-10-27)


### Features

* **standing:** retain decisions and topic threads beside the paragraph ([ad4c86b](https://github.com/mikeng-io/nora/commit/ad4c86bae9425d1d2530fc8d9672892aff316c82))


### Bug Fixes

* **standing:** report uncheckable threads and preserve timestamp groups ([8e613d0](https://github.com/mikeng-io/nora/commit/8e613d0ee6f4f7b66444522d9052766de1ce3d9f))
* **standing:** retain ledger read provenance in turn records ([1061d4e](https://github.com/mikeng-io/nora/commit/1061d4e0c29d58647313276a16f98a23c32fb6d1))

## [7.4.0](https://github.com/mikeng-io/nora/compare/v7.3.1...v7.4.0) (2026-10-27)


### Features

* **app:** downloads conversion, thumbnails, and 24h retention ([20c7c85](https://github.com/mikeng-io/nora/commit/20c7c856e4ccc2d1519cb546d43cff06d4e8b9d5))


### Bug Fixes

* **app:** conversion round 3 — muxer-safe staging, job-dir thumbs, owned publish ([4d5d8c0](https://github.com/mikeng-io/nora/commit/4d5d8c05bc156165ec8f465d4a6c690d51a478bc))
* **app:** downloads round-2 review — thumb collisions, atomic convert, sweep ([7956a11](https://github.com/mikeng-io/nora/commit/7956a1117e2ae1cb692c1530f585fd9a45cad6d2))
* **app:** single-entry conversion finish, delete-safe publish ([ef45bc0](https://github.com/mikeng-io/nora/commit/ef45bc0a465f4f05a97b2a6d7a6110eda6d9699a))

## [7.3.1](https://github.com/mikeng-io/nora/compare/v7.3.0...v7.3.1) (2026-10-27)


### Security

* **portal:** show presence only to people who share a circle ([6d8ce7c](https://github.com/mikeng-io/nora/commit/6d8ce7c691f37fb3e10e8334edad67c67dc9d0a5))

## [7.3.0](https://github.com/mikeng-io/nora/compare/v7.2.0...v7.3.0) (2026-10-27)


### Features

* **app:** /downloads, a yt-dlp batch downloader for the circle ([6f06f73](https://github.com/mikeng-io/nora/commit/6f06f73e5514a25dd3ba753f17af38fc5737fd93))


### Bug Fixes

* **app:** close downloads SSRF and lifecycle holes found in review ([5c7ad51](https://github.com/mikeng-io/nora/commit/5c7ad51a5e979982c490ed32ffa7699093d96643))
* **app:** downloads round 2 — cancel during DNS, write-storm guard, named volume ([6cdc2ea](https://github.com/mikeng-io/nora/commit/6cdc2ea4dc0850dab879a038fa1a1a76e8bbe781))

## [7.2.0](https://github.com/mikeng-io/nora/compare/v7.1.2...v7.2.0) (2026-10-27)


### Features

* **standing:** retain each turn's exchange with its disclosure requirements ([68d5101](https://github.com/mikeng-io/nora/commit/68d5101e4bc8f240058e45e7edc4be29530813ee))


### Bug Fixes

* **profile:** bound collective source inputs and allow refusal repair ([2fe0e69](https://github.com/mikeng-io/nora/commit/2fe0e699c852912fa2265a196d006ceda49b6d25))
* **profile:** leave the collective repair call room for non-source context ([cc1fe22](https://github.com/mikeng-io/nora/commit/cc1fe2271f9421ae57a0600b3e5852bd07b34b62))
* **standing:** preserve turn citation restrictions and revision evidence ([5372baf](https://github.com/mikeng-io/nora/commit/5372baf5e8c38027ae7ae5012386bfbb9ebefc19))

## [7.1.2](https://github.com/mikeng-io/nora/compare/v7.1.1...v7.1.2) (2026-10-27)


### Bug Fixes

* **record:** clarify extraction time objects and label failure stages ([b298c51](https://github.com/mikeng-io/nora/commit/b298c51dc16535e5662ada11284ff70d03f5ce8a))

## [7.1.1](https://github.com/mikeng-io/nora/compare/v7.1.0...v7.1.1) (2026-10-27)


### Bug Fixes

* **openwiki:** normalize gateway completion IDs and retain safe failure diagnostics ([f2b5746](https://github.com/mikeng-io/nora/commit/f2b57465e5e41f3389fc6dad43da66ba786cdc2b))

## [7.1.0](https://github.com/mikeng-io/nora/compare/v7.0.0...v7.1.0) (2026-10-27)


### Features

* **topology:** give rooms unique names and richer directory entries ([18d0915](https://github.com/mikeng-io/nora/commit/18d0915f897ef5713d0ae1c3d13a2b8cf0f2ab80))


### Bug Fixes

* **topology:** keep the room list when a culture read fails ([26e146f](https://github.com/mikeng-io/nora/commit/26e146f14be34eb08199e79e8357c5fc51caa133))

## [7.0.0](https://github.com/mikeng-io/nora/compare/v6.8.7...v7.0.0) (2026-10-27)


### ⚠ BREAKING CHANGES

* **config:** name memory configuration by its role ([ea36e50](https://github.com/mikeng-io/nora/commit/ea36e5087bf3a36ee4259cc53f4c4305f968deda))


### Bug Fixes

* **record:** preserve upgrade constraint names while restoring downgrade names ([404eb79](https://github.com/mikeng-io/nora/commit/404eb79c18fc04afecc00e17819eb4e54a69b905))
* **record:** restore original constraint names on extraction downgrade ([c7bc886](https://github.com/mikeng-io/nora/commit/c7bc8863eee2931dfe881f5b954ed4c8e055c79e))


### Changes

* **config:** name memory configuration by its role ([ea36e50](https://github.com/mikeng-io/nora/commit/ea36e5087bf3a36ee4259cc53f4c4305f968deda))

## [6.8.7](https://github.com/mikeng-io/nora/compare/v6.8.6...v6.8.7) (2026-10-27)


### Bug Fixes

* **profile:** guide collective writers to repair refusals and settle ([7974a3d](https://github.com/mikeng-io/nora/commit/7974a3d4dcb03269d33f49748654ff03da02bb4c))

## [6.8.6](https://github.com/mikeng-io/nora/compare/v6.8.5...v6.8.6) (2026-10-26)


### Bug Fixes

* **profile:** wake when new messages reach the batch threshold ([5c4d9e6](https://github.com/mikeng-io/nora/commit/5c4d9e6ed90918b5a3a2ac0fba6e3631cb16723e))


### Security

* **deps:** bump opentelemetry-api ([2f693dd](https://github.com/mikeng-io/nora/commit/2f693dd1fc93329bd3fae5e3b6d77ce206a7229a))

## [6.8.5](https://github.com/mikeng-io/nora/compare/v6.8.4...v6.8.5) (2026-10-26)


### Security

* **deps:** bump dompurify ([45b146b](https://github.com/mikeng-io/nora/commit/45b146b2e15ace20baec8f808e0bd98947fa4985))

## [6.8.4](https://github.com/mikeng-io/nora/compare/v6.8.3...v6.8.4) (2026-10-26)


### Changes

* **visuals:** use 2D Nora art outside 3D modeling ([c5060af](https://github.com/mikeng-io/nora/commit/c5060aff5018e05a518fdfa7254f0adb4a902701))

## [6.8.3](https://github.com/mikeng-io/nora/compare/v6.8.2...v6.8.3) (2026-10-25)


### Changes

* **visuals:** unify Mini Nora eyes and inventory visual assets ([ef324b3](https://github.com/mikeng-io/nora/commit/ef324b30f3e22921107f6c8c3fb23d0e79eced06))

## [6.8.2](https://github.com/mikeng-io/nora/compare/v6.8.1...v6.8.2) (2026-10-25)


### Bug Fixes

* **profile:** name a room by its policy label, never its platform conversation id ([1483383](https://github.com/mikeng-io/nora/commit/1483383bf9ad71c710f80b2a89ecb92af9e4ed96))

## [6.8.1](https://github.com/mikeng-io/nora/compare/v6.8.0...v6.8.1) (2026-10-25)


### Changes

* **egress:** disable optional content filters by default ([9ed6675](https://github.com/mikeng-io/nora/commit/9ed6675743c58300301cdfd92080b24b7e8ce65d))

## [6.8.0](https://github.com/mikeng-io/nora/compare/v6.7.0...v6.8.0) (2026-10-25)


### Features

* **record:** write message readings for new arrival batches ([35f68cd](https://github.com/mikeng-io/nora/commit/35f68cdc3583430e1738f0cd9f198848df790a71))


### Bug Fixes

* **record:** count reported reading tokens during ledger outages ([6470db5](https://github.com/mikeng-io/nora/commit/6470db5a270d68021089a00bafc017a5b12e7901))
* **record:** integrate readings with context and schema contracts ([dbb58d9](https://github.com/mikeng-io/nora/commit/dbb58d9edf6d4e4fd16159565e88b66a5d818fe3))
* **record:** preserve source control characters through assembly ([771aa22](https://github.com/mikeng-io/nora/commit/771aa22d0a377a5e710a6aa81409f47f74a3c0bd))

## [6.7.0](https://github.com/mikeng-io/nora/compare/v6.6.16...v6.7.0) (2026-10-23)


### Features

* **version:** list published releases and read exact release notes ([d050e43](https://github.com/mikeng-io/nora/commit/d050e436b77f7218b0e6b813a348b7044a5693aa))


### Bug Fixes

* **version:** read named release notes without listing ([b70352d](https://github.com/mikeng-io/nora/commit/b70352d66b546fe346ab575c7eff82dfc17d8cce))
* **version:** satisfy release tool gate checks ([e2f312a](https://github.com/mikeng-io/nora/commit/e2f312ab56e15864a76ae25ec4e0c12a4c665b5e))


### Changes

* **version:** read releases from the shipped manifest ([0077c11](https://github.com/mikeng-io/nora/commit/0077c11540b969777928a5f7c7b0f695e3ef98f2))

## [6.6.16](https://github.com/mikeng-io/nora/compare/v6.6.15...v6.6.16) (2026-10-23)


### Bug Fixes

* **security:** preserve newer sign-ins and refuse unsafe OAuth state ([6812a6f](https://github.com/mikeng-io/nora/commit/6812a6f1ac3383c035a8b4c9d75a95c2c24639f2))


### Security

* **hardening:** keep local state private and isolate eval and portal sessions ([837d5bf](https://github.com/mikeng-io/nora/commit/837d5bf977049fbe3afa468eee49d6def0b3bdf5))

## [6.6.15](https://github.com/mikeng-io/nora/compare/v6.6.14...v6.6.15) (2026-10-23)


### Bug Fixes

* **voice:** preserve valid audio and enforce configured guild admission ([1cf9f01](https://github.com/mikeng-io/nora/commit/1cf9f0177b4b34e52c4b2a62b05182b12b1185a0))


### Security

* **voice:** authenticate the voice event route and dispose a discarded core's sweep timer ([e5ecee6](https://github.com/mikeng-io/nora/commit/e5ecee602adf12e0554cc5e672e11de8be981d46))
* **voice:** authenticate voice ingress and bound retained work ([e8e23e8](https://github.com/mikeng-io/nora/commit/e8e23e8a05a1196f1eb1d603f635e8ccac28795c))

## [6.6.14](https://github.com/mikeng-io/nora/compare/v6.6.13...v6.6.14) (2026-10-23)


### Security

* **bridge:** authenticate the alert bridge, bound inbound bodies and streams ([eba96b2](https://github.com/mikeng-io/nora/commit/eba96b29667f32f55e7b777ecef0a0d7d16a4add))
* **bridge:** bound refused bodies, drop the stream lifetime, scrub an obsolete cookie paste ([3bfa9f1](https://github.com/mikeng-io/nora/commit/3bfa9f1fd1e6204c0b0a9d6994ac0f5c184b0704))
* **bridge:** leave a cookie paste the bridge did not consume alone ([ecc985d](https://github.com/mikeng-io/nora/commit/ecc985d11662ffdeecb44c7d9a9e9e127427be51))
* **bridge:** scrub a cookie paste only when it repeats the jar's session ([06a1b89](https://github.com/mikeng-io/nora/commit/06a1b89dd9ee371c642157d2f3c4aa7594bee181))
* **bridge:** the session probe sends the bridge bearer, and a newer cookie paste is kept ([4ed52e8](https://github.com/mikeng-io/nora/commit/4ed52e89fbc2451ec31ced0eef84c055c3ccbc4f))

## [6.6.13](https://github.com/mikeng-io/nora/compare/v6.6.12...v6.6.13) (2026-10-23)


### Security

* **computer:** verify Chrome revocation before exposing CDP ([62076ca](https://github.com/mikeng-io/nora/commit/62076ca18ed7cc3884e07b90021beca432392c8f))

## [6.6.12](https://github.com/mikeng-io/nora/compare/v6.6.11...v6.6.12) (2026-10-23)


### Security

* **build:** patch emSign trust in installers and deployment fetches ([ccfdfea](https://github.com/mikeng-io/nora/commit/ccfdfea7f3f9eab2d345ec7767f3212366785146))

## [6.6.11](https://github.com/mikeng-io/nora/compare/v6.6.10...v6.6.11) (2026-10-23)


### Security

* **voice:** contain spike WAV output for 8764_82704 ([8fb6e73](https://github.com/mikeng-io/nora/commit/8fb6e73c666e38e055dc74fb26e9a99d28a249a2))

## [6.6.10](https://github.com/mikeng-io/nora/compare/v6.6.9...v6.6.10) (2026-10-23)


### Security

* **asserts:** raise, not assert, where python -O would strip the check ([8ce4e37](https://github.com/mikeng-io/nora/commit/8ce4e37762f9303cd1ac52400e9111277cce4c1a))
* **sidecar:** contain the guild state file name under STATE_DIR ([e2894bb](https://github.com/mikeng-io/nora/commit/e2894bbb8ba3ce63553d29f6ae0eaa50e81960ab))
* **sidecar:** read the state file without following a link and create its temp file exclusively ([d60231d](https://github.com/mikeng-io/nora/commit/d60231d211238bef32e1286c926d7ce2b35a1ce3))

## [6.6.9](https://github.com/mikeng-io/nora/compare/v6.6.8...v6.6.9) (2026-10-23)


### Security

* **site:** allow only the page's own inline scripts by hash instead of 'unsafe-inline' ([efa0bd1](https://github.com/mikeng-io/nora/commit/efa0bd1c477ad354dc0d745a1e72862749a1c34a))

## [6.6.8](https://github.com/mikeng-io/nora/compare/v6.6.7...v6.6.8) (2026-10-23)


### Security

* **deps:** bump the base-images group across 2 directories with 2 updates ([eb5742a](https://github.com/mikeng-io/nora/commit/eb5742aca862cdd50e730d19c2b695252b8aba71))

## [6.6.7](https://github.com/mikeng-io/nora/compare/v6.6.6...v6.6.7) (2026-10-23)


### Bug Fixes

* **agent:** declare the requester on the DocumentPublisher read protocol ([755be46](https://github.com/mikeng-io/nora/commit/755be4628cf60b5eea445c496e25af317c78b1a4))


### Security

* **authz:** bound and parallelise the document reads behind subagent_list ([3887883](https://github.com/mikeng-io/nora/commit/388788350bb68c471bc84c23d7fc322d1f7348b7))
* **authz:** re-check grants at delivery, read, revise and relay ([7c7bb31](https://github.com/mikeng-io/nora/commit/7c7bb31cc6737a8fde60d6c3c3d5ea673efdb106))
* **authz:** show a sub-agent document only where its audience reads it ([556b8c5](https://github.com/mikeng-io/nora/commit/556b8c557994b31554e748e8c6cd9adefa5b0ce9))
* **authz:** title a sub-agent result document for the viewer, never the requester ([2d8664a](https://github.com/mikeng-io/nora/commit/2d8664a68e7ab5a402376494f5305f4b49e6cd0d))
* **documents:** list the visible documents before the limit, not after it ([d5b95c9](https://github.com/mikeng-io/nora/commit/d5b95c9ac3a51b8871bb2e6b65c9f6c7e130c0f0))

## [6.6.6](https://github.com/mikeng-io/nora/compare/v6.6.5...v6.6.6) (2026-10-23)


### Security

* **agent:** bound model tool batches by one process-wide in-flight limit ([d088561](https://github.com/mikeng-io/nora/commit/d088561c0548342769c8fc3b6bbefaff0a34e29e))
* **agent:** count a clip whose row failed to write against the cap in-process ([ce3c45d](https://github.com/mikeng-io/nora/commit/ce3c45d040e00efa73772884661dedf4dc6e4987))
* **agent:** give each MCP server one aggregate budget across its tools ([8a79857](https://github.com/mikeng-io/nora/commit/8a798572fa67ca94e710d314f00fe3fced0e5d83))
* **agent:** hold a cancelled clip write and reconcile past a failing row ([9587bff](https://github.com/mikeng-io/nora/commit/9587bff74006f1857cf978f29e741d8f579c00ab))
* **agent:** hold the pass ceiling for a parallel Dreamer batch in before-tool ([31292d4](https://github.com/mikeng-io/nora/commit/31292d4e095f223bc5232be5f5770922caa2f4f0))
* **agent:** make the browse task's terminal outcome one-way ([0891347](https://github.com/mikeng-io/nora/commit/0891347df152e73f7fcb229596fd02ca15291641))
* **agent:** reconcile held video rows before each cap read, count only today's ([029423f](https://github.com/mikeng-io/nora/commit/029423fb72b424a5ea38434ab0f99dfa31622533))
* **agent:** reserve tool slots for user turns so background work cannot starve them ([198972e](https://github.com/mikeng-io/nora/commit/198972edf9ce4c5cfef7efa5b34274607e3addd6))
* **browser:** check the read-only action policy before each page input ([945c7f2](https://github.com/mikeng-io/nora/commit/945c7f2ae15f6a47e8da249131734a49d8f4af64))

## [6.6.5](https://github.com/mikeng-io/nora/compare/v6.6.4...v6.6.5) (2026-10-23)


### Security

* **egress:** a bearing on both numbers of a pair is a coordinate ([6a2093a](https://github.com/mikeng-io/nora/commit/6a2093a99a7145a16d28baa4e66820ca22e593a4))
* **egress:** a comma or slash pair of two-digit parts is a coordinate ([a3a418e](https://github.com/mikeng-io/nora/commit/a3a418ecfe3c3235bd91c4e8b0e21e66be0d1c55))
* **egress:** a lone decimal is a coordinate only after a degree sign ([e7bfdfa](https://github.com/mikeng-io/nora/commit/e7bfdfad13d3304e6d8e5c66119de4ea79255043))
* **egress:** coordinate pairs, phone separators, invisible marks and labels ([15a894d](https://github.com/mikeng-io/nora/commit/15a894da8e4f05f1c75fff0b33ce8a9b99af5827))
* **egress:** reply guard catches separated coordinates, short numbers and invisible characters ([e67e300](https://github.com/mikeng-io/nora/commit/e67e3006f6a6618c0fb2b9d424963c74ef40ea4e))
* **memory:** the packet screens each person name before the model reads it ([4740adc](https://github.com/mikeng-io/nora/commit/4740adc314657275942ae7194f48b67a190dc54f))
* **summons:** a refused disclosure clears its staged text, and a 503 stays ambiguous ([da5d5f2](https://github.com/mikeng-io/nora/commit/da5d5f2b60ee84af7ab18d70603d32d8c403f4a3))
* **summons:** the public comment body is checked after its last transform ([2f15fa3](https://github.com/mikeng-io/nora/commit/2f15fa3189abba25426b133bc555c1744b39f979))

## [6.6.4](https://github.com/mikeng-io/nora/compare/v6.6.3...v6.6.4) (2026-10-22)


### Security

* **build:** pin every base image to a digest and let Dependabot keep them current ([3ce8640](https://github.com/mikeng-io/nora/commit/3ce86406bea0c61a003bc12fc37a950691af0a88))

## [6.6.3](https://github.com/mikeng-io/nora/compare/v6.6.2...v6.6.3) (2026-10-22)


### Security

* **deps:** pin patched undici, pino, dompurify, es-module-lexer and vitest ([31b2579](https://github.com/mikeng-io/nora/commit/31b2579daa420deeee7a9c4141e13d97e3900f48))

## [6.6.2](https://github.com/mikeng-io/nora/compare/v6.6.1...v6.6.2) (2026-10-22)


### Security

* **deps:** bump deploy/stt lock past seven Aikido medium advisories ([74747d3](https://github.com/mikeng-io/nora/commit/74747d329e438fdcd86b29e7969ff78b638bf172))

## [6.6.1](https://github.com/mikeng-io/nora/compare/v6.6.0...v6.6.1) (2026-10-22)


### Bug Fixes

* **security:** close the Aikido high-triage review findings ([142e275](https://github.com/mikeng-io/nora/commit/142e2759e59cb8722c53a0f5d4056427c34e6a7e))
* **security:** quarantine unresolved events and keep bridge tokens off argv ([5cbda28](https://github.com/mikeng-io/nora/commit/5cbda28d38ecf52490d106e1519a593fcd3366f1))


### Security

* **bridge:** require a bearer token on every transport route ([542d4aa](https://github.com/mikeng-io/nora/commit/542d4aaa9196fd788c15f539c5ad022c701d11de))
* **cognition:** scope event reads to the unit and refuse ambiguous room ids ([4e1d70a](https://github.com/mikeng-io/nora/commit/4e1d70affeeba9d01b21fe10338e18e2dfdcc95c))
* **computer:** stage restores outside the workspace tree ([7ef970a](https://github.com/mikeng-io/nora/commit/7ef970a5a646bba4b28b06afca9cd498245d9c85))
* **deps:** update the Instagram bridge to Go 1.27.3 ([71927c5](https://github.com/mikeng-io/nora/commit/71927c5ce332708a14fb835c31d52df2da3af8f1))
* **web:** send CSP and HSTS and run the site as non-root ([ff7a383](https://github.com/mikeng-io/nora/commit/ff7a383d23085112eed8fd09821cc6563baa9dcc))

## [6.6.0](https://github.com/mikeng-io/nora/compare/v6.5.1...v6.6.0) (2026-10-22)


### Features

* **character:** compose structured cards from dated source evidence ([bf7bd55](https://github.com/mikeng-io/nora/commit/bf7bd55c3202af7d4ace2c6fb5ca20008bdcf141))


### Bug Fixes

* **character:** bind collective location reads to cited subjects ([b5fb271](https://github.com/mikeng-io/nora/commit/b5fb271b4fd7b65c87d1e2d4337ffbe9894fc006))
* **character:** bound routine history and filter Cognition location lines ([bd46c0b](https://github.com/mikeng-io/nora/commit/bd46c0b6e5b09eb4e1564c2e919f626f7f9ad3cd))
* **character:** retain historical routines and settle unchanged revisions ([9248715](https://github.com/mikeng-io/nora/commit/924871523146d0ecb8cac846a26684fef559c45e))
* **deploy:** retain private failures and retry safe preflight ([cc3ca23](https://github.com/mikeng-io/nora/commit/cc3ca23b497bfd4fb66c4d512278ea4aeb08dd63))
* **snapshot:** annotate the sequence maximum SQL expression ([3e6639e](https://github.com/mikeng-io/nora/commit/3e6639ec3c4a8ba71fb940a1ceb8dd2113e86ee6))


### Security

* patch STT dependency, quote snapshot identifiers and add CSP ([d396646](https://github.com/mikeng-io/nora/commit/d396646c4029961a5a1261b93244509c67fa3952))

## [6.5.1](https://github.com/mikeng-io/nora/compare/v6.5.0...v6.5.1) (2026-10-22)


### Security

* **containers:** patch Prometheus and complete runtime validation ([9d64b46](https://github.com/mikeng-io/nora/commit/9d64b468ef5b0d93a18bb77a1cdad0254ced03e3))
* **containers:** upgrade Node services to supported Node 24 ([c0faa25](https://github.com/mikeng-io/nora/commit/c0faa251ff3035a3ed018d452d8be280b02a4dc0))

## [6.5.0](https://github.com/mikeng-io/nora/compare/v6.4.2...v6.5.0) (2026-10-21)


### Features

* **computer:** share group workspaces and browser state across each unit ([d54cea5](https://github.com/mikeng-io/nora/commit/d54cea5ce662a9d82bbab808bed680ec175e2b48))


### Bug Fixes

* **computer:** preserve rollback profiles and refuse interrupted retirement plans ([a51ddb8](https://github.com/mikeng-io/nora/commit/a51ddb8d48f63af6e5230252932093bedfe9a218))
* **computer:** retire verified migration quarantine and stop before rollback ([a086c11](https://github.com/mikeng-io/nora/commit/a086c11b3771aec8c7a300ee444a86b302fe7860))
* **deps:** patch certifi and Vite security vulnerabilities ([517edcf](https://github.com/mikeng-io/nora/commit/517edcf51cd1d355910fc316f79c6bb67ab1bdfe))
* **voice-lab:** require STT_URL and surface curl failures ([bfee4fc](https://github.com/mikeng-io/nora/commit/bfee4fc48f1d792aa50aa54bfdce12dec200386e))


### Security

* **deps:** override vulnerable Pillow and PyNaCl caps ([ea763e5](https://github.com/mikeng-io/nora/commit/ea763e5bf508a611069b73fe33b8e56f7e7c0d69))

## [6.4.2](https://github.com/mikeng-io/nora/compare/v6.4.1...v6.4.2) (2026-10-21)


### Bug Fixes

* **persona:** keep rollup source reads within their timeout ([e55ba7d](https://github.com/mikeng-io/nora/commit/e55ba7df6332dc3be2919813f87fad645db1b8a0))
* **social:** finish empty terminal pages after a cursor ([a948571](https://github.com/mikeng-io/nora/commit/a94857117db1696c380ce6676d181b91d5864db6))
* **social:** retry empty backfills when the profile reports posts ([0ea6c58](https://github.com/mikeng-io/nora/commit/0ea6c5830bc87c2c235ce987a444e7cb7c98543a))
* **social:** stop retrying private grids Nora does not follow ([027b715](https://github.com/mikeng-io/nora/commit/027b715467651dab1064e0a2f7a68278de3962b4))

## [6.4.1](https://github.com/mikeng-io/nora/compare/v6.4.0...v6.4.1) (2026-10-20)


### Bug Fixes

* **wiki:** render Mermaid diagrams in the public guides ([7b463e9](https://github.com/mikeng-io/nora/commit/7b463e926ee48f490da3662a2e16e58d4a8aefd0))

## [6.4.0](https://github.com/mikeng-io/nora/compare/v6.3.1...v6.4.0) (2026-10-20)


### Features

* **wiki:** publish technical guides with a Nora landing page ([363716f](https://github.com/mikeng-io/nora/commit/363716ffe609992c2bf61d02405be8ba4435b1b6))


### Bug Fixes

* **memory:** consider authored transport metadata when retaining observations ([e283c65](https://github.com/mikeng-io/nora/commit/e283c65556311d8974f562968b16c980aabbd22f))
* **memory:** preserve unspecified participants in plural reports ([898a813](https://github.com/mikeng-io/nora/commit/898a8139220b3ad94e01819a1b2962d037fbb74b))
* **memory:** retain qualified reports and observed transport evidence ([f10a64e](https://github.com/mikeng-io/nora/commit/f10a64e13c7aa33fe3b89c3d264fcb10cacb45af))
* **wiki:** keep Ollama completion IDs unique during generation ([ec9dffd](https://github.com/mikeng-io/nora/commit/ec9dffdc4f466723ae5288413a8af21e108d3d20))
* **wiki:** preserve verification when resuming interrupted generation ([e81320a](https://github.com/mikeng-io/nora/commit/e81320a289d049a6d2942228e4ecc1dfa9e14db0))

## [6.3.1](https://github.com/mikeng-io/nora/compare/v6.3.0...v6.3.1) (2026-10-20)


### Bug Fixes

* **memory:** retry one cached-media account through the running Dreamer ([481d8b9](https://github.com/mikeng-io/nora/commit/481d8b9536554f5f2ec1518e8d4bd480f2b96d37))

## [6.3.0](https://github.com/mikeng-io/nora/compare/v6.2.0...v6.3.0) (2026-10-20)


### Features

* **memory:** recover cached media and revised person evidence ([7f6403f](https://github.com/mikeng-io/nora/commit/7f6403fc121be65b61cc2a1d61523b139fae94f3))
* **social:** adapt polling to posting rhythms and list Instagram posts ([31c4c80](https://github.com/mikeng-io/nora/commit/31c4c809ffb4cd7c29aa388f6bf439e2eca43a8f))
* **social:** import full history for opted-in members ([5950e13](https://github.com/mikeng-io/nora/commit/5950e13b78a6550cf47f43e7da08eb4738641b7e))


### Bug Fixes

* **deploy:** share the host lock outside container sticky tmp ([efd3099](https://github.com/mikeng-io/nora/commit/efd30995aa61dcd9284b9b08414bfc39e1d345c3))
* **deploy:** share the host lock outside container sticky tmp ([2606774](https://github.com/mikeng-io/nora/commit/260677497bd781c9ba6060f314770320677af1c3))
* **memory:** bound the initial cached-reading recovery sweep ([aae84e7](https://github.com/mikeng-io/nora/commit/aae84e721abb6a1a28efefb8cffb2a1f3c37ce56))
* **memory:** deliver rooted rollups and edits past consumed messages ([c0483be](https://github.com/mikeng-io/nora/commit/c0483bed734aa1cbb13dd580f854466cee76f921))
* **memory:** preserve historical source boundaries and rooted inputs ([d4db2fc](https://github.com/mikeng-io/nora/commit/d4db2fc5b1d6cea92989d777bb42b1eb2f1cea84))
* **memory:** preserve writer receipts for revised source packets ([e6283b3](https://github.com/mikeng-io/nora/commit/e6283b3e8f5459d7d9ebef2f0dc4d53414206a31))
* **memory:** render cached attachments missing legacy caption handles ([1a800ba](https://github.com/mikeng-io/nora/commit/1a800ba292117ae9f6b7e4524bff0ed57c5c1123))
* **memory:** retain subject response roots and question context ([e11242a](https://github.com/mikeng-io/nora/commit/e11242a317f4f8f32123481b0672597282d97ac8))
* **persona:** clarify cached attachment text as deduction premises ([9f7c912](https://github.com/mikeng-io/nora/commit/9f7c912e7760a05962a42468cc0c87d50c9edc8f))


### Security

* **deps:** patch critical dependencies and simplify public contributions ([74aff57](https://github.com/mikeng-io/nora/commit/74aff5715a8b370709be1400fa51e719a0ba3daf))
* **deps:** patch vulnerable dependencies across Nora services ([7796727](https://github.com/mikeng-io/nora/commit/77967278920943662f2090f4796c2ba47131ec23))


### Changes

* **web:** use Nora's selected portrait for repository and app icons ([c121330](https://github.com/mikeng-io/nora/commit/c1213308f3ae38b078f429fd43fb954a90101c33))

## [6.2.0](https://github.com/mikeng-io/nora/compare/v6.1.0...v6.2.0) (2026-10-19)


### Features

* **persona:** preserve explicitly stated additional identity attributes ([c4fce2d](https://github.com/mikeng-io/nora/commit/c4fce2da36923eacbc24627f7497ad781b185a75))
* **persona:** retain typed identity and resume historical recovery ([534f2ae](https://github.com/mikeng-io/nora/commit/534f2ae624803fad1dbf804d99f38fae9e272d7f))


### Bug Fixes

* **evals:** bind persona coverage to its original census ([fdfe705](https://github.com/mikeng-io/nora/commit/fdfe7056df0b9ed30a1c5b2a669331d86d37ef9c))
* **evals:** bind persona coverage to the complete original evidence ([475fc28](https://github.com/mikeng-io/nora/commit/475fc28206c18efd660e41955e79cda7fd227715))
* **evals:** clarify coverage JSON output and accept complete legacy objects ([cb13da7](https://github.com/mikeng-io/nora/commit/cb13da7c784d076b1d614d36fe726ffb0f0226b0))
* **evals:** coverage accepts complete JSON fences and requests low reasoning ([96d3c6e](https://github.com/mikeng-io/nora/commit/96d3c6e82bed5ef56f01ae5ad566fb30241895f5))
* **evals:** exclude restored rows from persona writer stimuli ([01d5282](https://github.com/mikeng-io/nora/commit/01d528214205f2353257be7dbe6311ec1d7850b3))
* **evals:** include declared evidence in persona writer sessions ([5d09b02](https://github.com/mikeng-io/nora/commit/5d09b02fecf19303cea500f655e8ec555b5613e1))
* **evals:** render readable source times for coverage judgments ([360e857](https://github.com/mikeng-io/nora/commit/360e857cba0564e42ad5d4e2f64b4e2328622812))
* **persona:** enforce disclosure and rendering switches on every read ([f17728b](https://github.com/mikeng-io/nora/commit/f17728bb97bdb3aca3bfc05c4b76e0a763f24d5f))
* **persona:** keep qualified plans out of settled identity claims ([d6b4fb1](https://github.com/mikeng-io/nora/commit/d6b4fb1287ca26e4bf3cb0e5eb765e7f951a783b))
* **persona:** preserve provenance for equivalent numeric attributes ([3caeec4](https://github.com/mikeng-io/nora/commit/3caeec432b13e125b03524de0727bf56188aad3f))
* **persona:** preserve source units for stated age and height ([2e10094](https://github.com/mikeng-io/nora/commit/2e10094f5bb2321c48f9d4309a86e25a749f7700))
* **persona:** preserve unit progress and resolved relationship targets ([9d310ad](https://github.com/mikeng-io/nora/commit/9d310add7371986f141f61fedd74199b1d394552))
* **persona:** retain grounding when rewording identity facts ([316536f](https://github.com/mikeng-io/nora/commit/316536f07f2e44f80493b14fec9ed63ff7a41ac0))

## [6.1.0](https://github.com/mikeng-io/nora/compare/v6.0.0...v6.1.0) (2026-10-19)


### Features

* **app:** Plans shows every event in the rooms you are in ([a15aaf3](https://github.com/mikeng-io/nora/commit/a15aaf3be7e60474e9d4ce6c2b791b1aed5421e6))
* **character:** read and cite a member's social items as evidence about them ([52fce44](https://github.com/mikeng-io/nora/commit/52fce44fdc8e4021d65f3b7d071eebf7b217eef7))
* **character:** read and cite a member's social items as evidence about them ([0ad9156](https://github.com/mikeng-io/nora/commit/0ad9156928e1eb18ea55b9394a6931e038d0157e))
* **cognition:** read a person's social items, with what each shows, in Cognition ([c3fbcb0](https://github.com/mikeng-io/nora/commit/c3fbcb07566493a04dfb7f4e29c50443b7dc229c))
* **cognition:** read a person's social items, with what each shows, in Cognition ([22e92eb](https://github.com/mikeng-io/nora/commit/22e92eb3e957dc3d15b64db3367c67ea12ab6a7d))
* **evals:** measure the memory graph surface on each sandbox's own index with real vectors ([357f6b0](https://github.com/mikeng-io/nora/commit/357f6b04dadbc8f61a889fd31c1c4111bb5888cc))
* **instagram-bridge:** list each post's and reel's likes, comments and reposts, and whether the author hid its likes ([13e03b6](https://github.com/mikeng-io/nora/commit/13e03b6eb80ae5fc2989049db1b87afb713c20e8))
* **memory:** a search near a district finds the events and outings that lie in it ([08c0700](https://github.com/mikeng-io/nora/commit/08c070097ab99500f06e88eed3f294e4413b1de7))
* **memory:** cut memory_search's answer by a token budget, a score gap and a measured dense floor ([934e7ff](https://github.com/mikeng-io/nora/commit/934e7ff45e2dbfbcfb332da972fc5446c5f4b6f5))
* **memory:** embed index nodes with Qwen3-VL into a two-stage halfvec index (V1) ([bf089d9](https://github.com/mikeng-io/nora/commit/bf089d9531fe21590bbe5c4950c61517a4ef047e))
* **memory:** find a social item by its tagged place where the room may hear where its author was ([83a0bda](https://github.com/mikeng-io/nora/commit/83a0bdacd9d9d2cdacdf7673d0ffeae3afee6e89))
* **memory:** find a social item by its tagged place where the room may hear where its author was ([6042dbf](https://github.com/mikeng-io/nora/commit/6042dbf6d9d9cf3e5c5a0395ab5795426abe3671))
* **memory:** index Instagram social items, person-scoped and opt-out at read ([52a279c](https://github.com/mikeng-io/nora/commit/52a279c26851fe6adafcdb5057c5b50b7f71e32e))
* **memory:** memory_search reaches a note from the messages it cites ([53bbd31](https://github.com/mikeng-io/nora/commit/53bbd31e89b63f48becbb94abd3effcd3c819536))
* **memory:** on the graph memory surface a tag turn carries what her memory holds that the message touches ([6f1b42f](https://github.com/mikeng-io/nora/commit/6f1b42fb606ea603192cd8d473c05d54580cfb84))
* **memory:** seed ask_memory from dense vectors and pinned exact matches (V2) ([ac977ce](https://github.com/mikeng-io/nora/commit/ac977cee58c196963c3b809a0a96b0f278655525))
* **memory:** she keeps a fact someone tells her about a person on the memory graph surface ([8b6d307](https://github.com/mikeng-io/nora/commit/8b6d307408c74755740f545729a99e49aca64ca9))
* **persona:** a member's social item is citable evidence about its author alone, never what a told line cites ([0d13867](https://github.com/mikeng-io/nora/commit/0d13867581bba1041ed9b683bf959fe8a4dabfc2))
* **persona:** mentions, disputes and the hypothesis verdict judge complete the person account ([ea34f4c](https://github.com/mikeng-io/nora/commit/ea34f4cfd3483c1c0d530add65f2aaf66cd4da87))
* **persona:** the Persona adaptor writes each person's typed lines in shadow, with the backfill as its first session ([26c4758](https://github.com/mikeng-io/nora/commit/26c47587b04f5b2b128d29f730b3c459c20a4323))
* **persona:** the room, unit and Nora Persona sessions, in shadow ([a9a0c72](https://github.com/mikeng-io/nora/commit/a9a0c72b7b5aa6f7c841074d2f8a7e0e68f697da))
* **rollup:** count members' social items on each day of the daybook ([ac397a4](https://github.com/mikeng-io/nora/commit/ac397a439b40352b07e338bac8a1230b87b3eab2))
* **social:** a tag the author adds, edits or removes later changes the place Nora searches ([e07cc1f](https://github.com/mikeng-io/nora/commit/e07cc1fcdea88deb9d53de0b2eedbc1eb5baabcd))
* **social:** a tagged place follows the author, changed or removed, and the index shows the current one ([61b1c99](https://github.com/mikeng-io/nora/commit/61b1c99f5fd1eef25b56f8c301941af8ac17101e))
* **social:** back-fill members' past posts and reels, page by page, from spare budget ([a6e16e9](https://github.com/mikeng-io/nora/commit/a6e16e924fe902d2e171cf82823072662217ff35))
* **social:** delete a story frame's bytes a week after it expires once the memory index has embedded it ([f27e6a3](https://github.com/mikeng-io/nora/commit/f27e6a3299e9f93ce859146791c1d2f99f19fa30))
* **social:** delete a story frame's media bytes after it expires, behind a switch ([be8ac5f](https://github.com/mikeng-io/nora/commit/be8ac5f6e84eb377a9cbfac7ba7bcf577d149b6e))
* **social:** perceive members' social items and wake Cognition once per read ([f663470](https://github.com/mikeng-io/nora/commit/f6634702b81074780f19bab4572e1e4de2e43917))
* **social:** poll members' Instagram stories and posts on a ceiling cadence ([b753859](https://github.com/mikeng-io/nora/commit/b753859d3fc1bb23c833ce6f59893a14c6a7212d))
* **social:** record and re-read each post's and reel's likes, comments and reposts, and show them in social_read ([bd29d20](https://github.com/mikeng-io/nora/commit/bd29d201b56f5ba2e13b9324f414b41ed3590279))
* **social:** record the place a post, reel or story frame is tagged with ([e9d5f55](https://github.com/mikeng-io/nora/commit/e9d5f55b60ec7fd9e8b40fed3810ba264a7ce26a))
* **social:** the social stream's source port, Instagram adapter and Record tables ([ffc8970](https://github.com/mikeng-io/nora/commit/ffc89706e0584ef6c335fa64c47c9257d2c86996))
* **tools:** one memory_search verb replaces ask_memory and search_memory_workspace ([72f1565](https://github.com/mikeng-io/nora/commit/72f156550d56ef4fbcf9cbdc3e1430a021b4decf))


### Bug Fixes

* **app:** every plan that is not confirmed wears its status, and a cancelled one exports as cancelled ([83e64e8](https://github.com/mikeng-io/nora/commit/83e64e8bc909807d3db1bd4c247e05672d187c03))
* **app:** the Plans page shows plans when one of them has lapsed ([522c479](https://github.com/mikeng-io/nora/commit/522c479032b0feeb6ceb1218f6fdcbdcfb18203a))
* **arch:** catch single-digit and glued issue numbers, and move orphaned metric comments ([3fd3da4](https://github.com/mikeng-io/nora/commit/3fd3da4e2a64ee84e4d2d7a1d4f82a9f010b0f4c))
* **blob:** rustfs keeps bytes through their whole idle window ([120c879](https://github.com/mikeng-io/nora/commit/120c8791b9c21f8d83beb476eb79d6b4ac81d659))
* **build:** run the bridge step when the gate cannot diff against origin/master ([bfe60d1](https://github.com/mikeng-io/nora/commit/bfe60d113003447fe4848330e5b2ad752ac0a778))
* **character:** a social batch abandoned twice is consumed, the wake floor is the read's time, the budget holds on the rendered block, and redaction closes its gaps ([efd8c15](https://github.com/mikeng-io/nora/commit/efd8c15fcccc4e1b4c40926b901b5bc42dfd8b9f))
* **character:** exclude an opted-out person's items under every id they were recorded under ([0f35d70](https://github.com/mikeng-io/nora/commit/0f35d7027d8e8a16517866debd8897bdcfdeeeec))
* **character:** hand every social item once, bound the block, and redact other people's handles ([f151035](https://github.com/mikeng-io/nora/commit/f151035c953a115a60b822b9ed2615a289888940))
* **character:** read a person's social items under their former ids too ([a8ce92a](https://github.com/mikeng-io/nora/commit/a8ce92a6f87c7d89a8f2590f40f2e207d92dfc4d))
* **character:** redact every @ run and every Instagram link tail, failing closed ([d61ae98](https://github.com/mikeng-io/nora/commit/d61ae98aa9dcadf0dbc2172f990bca09f7683f39))
* **character:** redact every caption handle but the author's own, whatever precedes the @ ([a8f6da5](https://github.com/mikeng-io/nora/commit/a8f6da5519adaeb5f0016659a3be438f4c083924))
* **character:** store the social cursor in a BIGINT and redact handles after CJK or full-width @ ([5db463f](https://github.com/mikeng-io/nora/commit/5db463f5c074d8613152f2d8e83238ab584cd15d))
* **character:** wake on new social items only, bound the block by the session's tokens, and redact handles in readings ([dfc6000](https://github.com/mikeng-io/nora/commit/dfc6000e29e28b68f2c3e22629f40c217b29d8b7))
* **cognition:** a moved reminder that is already firing is refused, and a bracketed note id links ([b829145](https://github.com/mikeng-io/nora/commit/b829145fd24ea3322c7fe18c254a3d8e92c9afe3))
* **cognition:** a note row names only the reminders this session can move or cancel ([4a9256a](https://github.com/mikeng-io/nora/commit/4a9256a6373be0abdafa7153e32681df1816a90a))
* **cognition:** a proactive reminder citing a note by its shown id dies with that note ([228c610](https://github.com/mikeng-io/nora/commit/228c61046a4051431c87c828c473e4b793a47b02))
* **cognition:** a session sees its own pending reminder on the note it is about ([73af463](https://github.com/mikeng-io/nora/commit/73af4630144fd53e5727326d9f572acff1cb2689))
* **cognition:** no tool hands a tag turn's model a person id, and every social reader is built through one helper ([05ba135](https://github.com/mikeng-io/nora/commit/05ba135895b027a2a96f73947fe9056b0680fcbf))
* **cognition:** social_read reads the person its agenda line names ([1895f7f](https://github.com/mikeng-io/nora/commit/1895f7f5845b6d9f5fb646c0307309d60636b883))
* **cognition:** social_read shows the place an item is tagged with now ([331f3e8](https://github.com/mikeng-io/nora/commit/331f3e8324c7ed2683bc7142079452d010b04dc1))
* **evals:** break stage-1 distance ties by id in the SQL two-stage arm, and kill the review's surviving mutants ([e2ac92c](https://github.com/mikeng-io/nora/commit/e2ac92c12d4dda108462404a1d7d11afa00274a9))
* **evals:** two tool_selection routing cases stop failing on wrong expectations ([f223c0d](https://github.com/mikeng-io/nora/commit/f223c0de8c6e9746c54758abc9a875461867a15c))
* **instagram-bridge:** list a member's collaboration posts instead of refusing the page ([463e283](https://github.com/mikeng-io/nora/commit/463e283211e5eff8bae209b2c7b150fb072164be))
* **instagram-bridge:** read a timeline page whose edges arrive with a field_exception beside them ([f011b3b](https://github.com/mikeng-io/nora/commit/f011b3b921b2ac25c9b3c1c104e0df34343b0b8a))
* **instagram-bridge:** read stories through the web app's GraphQL query and list a member's posts and reels ([d844eca](https://github.com/mikeng-io/nora/commit/d844eca9204c5eb498d2d9fb6beb48e864281791))
* **instagram-bridge:** refuse a timeline item whose owner fields name different accounts ([480e2bd](https://github.com/mikeng-io/nora/commit/480e2bdd221a401264799b061bbbf30fa4104842))
* **instagram-bridge:** restore the chan keyword the history scrub rewrote, and build the bridge in the gate ([d10702b](https://github.com/mikeng-io/nora/commit/d10702bfdcae378b6a43dff459261bc270d1aa5b))
* **memory:** a named place's hits survive the gap cut, the budget and the scope, and every area name is screened ([5d3004f](https://github.com/mikeng-io/nora/commit/5d3004fd525acc4d3eafae497aa54c6a45cab3b9))
* **memory:** cap pinned exact hits, case names, and read given-up nodes by their words ([c7139fa](https://github.com/mikeng-io/nora/commit/c7139fa087a27b648053ed2a5b5023f843679c5f))
* **memory:** find a line still waiting for its vector by its words, and answer partial only on a real backlog ([d160e91](https://github.com/mikeng-io/nora/commit/d160e91c2d3901c1c707aa16478253d3ac93fe97))
* **memory:** find an old social post memory_search matched, and end its answer only when nothing strong is left ([d38fb29](https://github.com/mikeng-io/nora/commit/d38fb29b46cfa1eb8e5d4efe46ed3a4e36c14909))
* **memory:** keep label-only social items unembedded and never default the opt-out ([d12fa28](https://github.com/mikeng-io/nora/commit/d12fa2810859b84b2afda4506ee5474ad44c4985))
* **memory:** keep memory_search's floor off a date-windowed question, so the line it asks about is still found ([0cc3207](https://github.com/mikeng-io/nora/commit/0cc32072fba290a8dd1c7195932853e3deb2a6dc))
* **memory:** keep the cosine floor on seeds so a hop still reaches an episode ([9ca0e17](https://github.com/mikeng-io/nora/commit/9ca0e17b103283b626390c66c091ae17c179a37c))
* **memory:** kinds narrow the seeds and the answer, not the hops ([e673515](https://github.com/mikeng-io/nora/commit/e673515c6fe4526e3533e0621024fcd65de900ea))
* **memory:** measure a node's wait from when its vector became owed, and never call an unreadable node fresh ([85c6328](https://github.com/mikeng-io/nora/commit/85c6328a434c35fb0e38a862afb9548ed7b9a03c))
* **memory:** no embedding input can wedge the worker, and the server runs the measured flags ([ebda388](https://github.com/mikeng-io/nora/commit/ebda388dd530b83f394ce2bb6701b14a4c2da4b5))
* **memory:** send images at the size the bake-off measured, not 1920 tokens ([c781c13](https://github.com/mikeng-io/nora/commit/c781c1399d9ea403750b35dd9d5864a5d2a9d55c))
* **memory:** the pre-fetch reads the graph's clock, is bounded and guarded, and pins at most PINNED_LIMIT pictures ([cabbf57](https://github.com/mikeng-io/nora/commit/cabbf5756ccee23678fb83ebfc38366c6952bf14))
* **migrations:** chain the watermark BIGINT migration after the social location node as 0114 ([8cf6419](https://github.com/mikeng-io/nora/commit/8cf6419792b8abfe7d61178b7affc96cae653776))
* **migrations:** reparent 0119_social_item_count onto the landed 0118_place_area ([4511902](https://github.com/mikeng-io/nora/commit/4511902cd216ffeaadf54e38c66cd9ebb0aaf1d1))
* **persona:** an account the verdict keys lost is code's unknown, never the model's empty-window call ([71eca0c](https://github.com/mikeng-io/nora/commit/71eca0c8501df0e5599011b5711fc891296a31cb))
* **rollup:** a member seen only through their posts starts no silence run ([93b1d15](https://github.com/mikeng-io/nora/commit/93b1d15ef93dba670b364ca2433fd1bf4e9270f7))
* **social:** a bridge outage no longer ends a member's back-fill ([8dbdea4](https://github.com/mikeng-io/nora/commit/8dbdea45cd8badc57e2c945c2db2c8d90844f2a1))
* **social:** a malformed location tag is no location, never a refused page or story ([1c7bdf3](https://github.com/mikeng-io/nora/commit/1c7bdf3c8256c7e9e0ac1e6f74dcd67e47b6b729))
* **social:** a restart that cannot read the login's history retries instead of forgetting a halt ([d5a10f1](https://github.com/mikeng-io/nora/commit/d5a10f10b875d9dd9a5fa5ec4fd48470dcdcabf6))
* **social:** a story read with no usable frame advances the marker, and counts advance only once their list read completes ([39fbed1](https://github.com/mikeng-io/nora/commit/39fbed140a9844859174db9d18579646660179c3))
* **social:** an opted-out person's items stay out under every id they were recorded under ([f8a52f6](https://github.com/mikeng-io/nora/commit/f8a52f612a1c1536b29684e348dad64f85fceb6f))
* **social:** an unreadable place is unknown, never a removed tag, and a halt stops the re-walk ([46a729e](https://github.com/mikeng-io/nora/commit/46a729ea1b7c61c924d107b33862cf2763384221))
* **social:** keep set-aside bytes across a crash and date a tombstone before its deletion ([b339f27](https://github.com/mikeng-io/nora/commit/b339f27faf07272526819e3eed7d3097d4aa00ea))
* **social:** keep the poller's halt, back-off and budget across a failed row and a restart ([c855d7f](https://github.com/mikeng-io/nora/commit/c855d7f57be1a29ba6e05d4dcea84dcd3737f27f))
* **social:** raise a read's stimulus only within two days of it, and charge a video's text pass ([93ec170](https://github.com/mikeng-io/nora/commit/93ec170a58c7a1a3dce35f3e60e6cf5bc5cb45dc))
* **social:** record listed posts and reels from the list itself, with their media ([27fd4f2](https://github.com/mikeng-io/nora/commit/27fd4f2d9b34f1096946e241e6c26fdecf24ce54))
* **social:** renumber the back-fill migration after the memory index's 0111 ([8f92cd0](https://github.com/mikeng-io/nora/commit/8f92cd0658e71f66693feec2e89354f0341f9d20))
* **social:** the back-fill leaves new posts to the poll and gives up a cursor that keeps failing ([7ce45a2](https://github.com/mikeng-io/nora/commit/7ce45a296488387435d7ef35d6f3ba36c45cce25))
* **tools:** memory_search keeps a room turn in its own channel when the index is off ([f441701](https://github.com/mikeng-io/nora/commit/f441701f85a74c3b8124e83796bea3ba102b5869))
* **tools:** memory_search says unavailable for a person this room does not list ([49d502e](https://github.com/mikeng-io/nora/commit/49d502e828bbe769d9a000733d5ce65261198a38))
* **tools:** memory_search searches the unit only for the cognition session ([8e33c98](https://github.com/mikeng-io/nora/commit/8e33c98140ce8a8ff5ee90b89681e1849dcfc536))
* **tools:** one source for the memory surface, culture reads its own room ([2739833](https://github.com/mikeng-io/nora/commit/27398336d82aae4172b3d580f6b957c7377ded49))


### Security

* **app:** a room's events reach a member only past the chat path's lineage and surprise checks ([17d82f3](https://github.com/mikeng-io/nora/commit/17d82f339bfd28c8ddd868b2d93acfa604cf9b50))


### Changes

* **memory:** memory_search says a span of time is record_query's, and who mentioned something accepts either read on the graph ([ebd0a64](https://github.com/mikeng-io/nora/commit/ebd0a642c8ead38d6ba0990a67c153bcfe5479da))
* **memory:** memory_search sends a read with no topic to record_query, not a topic searched within a span ([4ea626c](https://github.com/mikeng-io/nora/commit/4ea626c44bcf9c768d4d66fe676e6d681ad1b948))
* **memory:** the memory-graph surface holds no Honcho tool, and memory_search's budget is 8 ([1d97e54](https://github.com/mikeng-io/nora/commit/1d97e5402d5761d03c3dce7f2c6c838d5258d757))
* **persona:** renumber settled_at migration to 0120, behind 0118_place_area and 0119_social_item_count ([e5c0ea2](https://github.com/mikeng-io/nora/commit/e5c0ea28517923ca75d7dfe096822e1bee526c25))
* **persona:** reparent 0120_persona_settled_at onto 0118_place_area, one alembic head ([5b6089f](https://github.com/mikeng-io/nora/commit/5b6089f53706f9e8d7dbc375b1cbff73491eff23))
* **persona:** reparent 0120_persona_settled_at onto 0119_social_item_count ([c02dfcd](https://github.com/mikeng-io/nora/commit/c02dfcd1fba992ff7ed38864e74dcd84b46e1167))


### Doctrine

* **conduct:** a plan made for now is a placeholder the real one settles ([34a53ff](https://github.com/mikeng-io/nora/commit/34a53ffcf69d75661aa4bad98efb0c68263a9312))
* **conduct:** the placeholder line names provisional, not the bare 「先」, and keeps a leftover that costs ([b8a907c](https://github.com/mikeng-io/nora/commit/b8a907cae7449827d54d5725b78d2567dd8ec7e8))
* **memory:** memory wording reads true on both memory surfaces ([6eb8a44](https://github.com/mikeng-io/nora/commit/6eb8a448ac01b5d283154d37134fbda309e4e94f))
* **memory:** the speaker-fact rule is a condition, and a failed write still says so ([2922ab2](https://github.com/mikeng-io/nora/commit/2922ab2e5e76451abaa4f95673d4f72c85cc7a1d))

## [6.0.0](https://github.com/mikeng-io/nora/compare/v2.0.0...v6.0.0) (2026-10-15)


### ⚠ BREAKING CHANGES

* **release:** the [version] section is no longer accepted in config.toml; remove it from the live config before deploying this image.

### Features

* **app:** add public privacy and terms pages for Google's consent screen ([2ab589d](https://github.com/mikeng-io/nora/commit/2ab589dbf171af25a6e2d0337a01a3130aa5edf0))
* **app:** make the privacy policy and terms cover Nora as a whole ([4d02988](https://github.com/mikeng-io/nora/commit/4d02988a8d1cee27b8b8f0eec630045253fb6a9a))
* **artifacts:** links open files in the portal behind sign-in, listed under Artifacts ([7ba07bd](https://github.com/mikeng-io/nora/commit/7ba07bd2bb536a6689da2e8bd7707690fc2c7d5a))
* **computer:** computer_browse, a whole browser task done by a sub-agent in the room's browser ([b9c4371](https://github.com/mikeng-io/nora/commit/b9c43718f8756e9bdf6824102102e849795f2c6a))
* **computer:** let any file but a web page leave the computer ([a4b2bba](https://github.com/mikeng-io/nora/commit/a4b2bba329999310b5c29df5d726ffc51f26289b))
* **evals:** a lessons counterfactual tier and corpus to measure the &lt;lessons&gt; block ([167ea14](https://github.com/mikeng-io/nora/commit/167ea14961f093975c9267879840c4ba0105ae9b))
* **evals:** add the retrieval bake-off's harness, adapters and decision rule ([b8e3715](https://github.com/mikeng-io/nora/commit/b8e3715e6c726e6ceba46125bb49aea5ab44cfe4))
* **evals:** add the text-to-image pool and queries for the retrieval bake-off ([16aa1ab](https://github.com/mikeng-io/nora/commit/16aa1ab313b8036a58b400a46a96c811d3458775))
* **evals:** pick among passing arms by the fewest total misses (operator ruling 2026-10-16) ([63e8d83](https://github.com/mikeng-io/nora/commit/63e8d83a796909874b1ec32902fffe01772d47ee))
* **evals:** run bake-off stage 2 through the HF adapter at full precision ([eef4775](https://github.com/mikeng-io/nora/commit/eef47756deca9fdfbb6b704e10dcf4694ac59d92))
* **evals:** run Qwen3-VL-Embedding-2B and jina-embeddings-v5-omni-small in their official formats ([fb582e9](https://github.com/mikeng-io/nora/commit/fb582e9030551bbee19fa43723919ecc84e174e1))
* **evals:** run the bake-off on all three slices, with EmbeddingGemma 2 as stage 2 ([2ca42b8](https://github.com/mikeng-io/nora/commit/2ca42b84d35ac3c5c4d3483d089834d82f9b51cc))
* **evals:** run the recall journeys on a full restore, judged on what she remembers ([724a801](https://github.com/mikeng-io/nora/commit/724a801c0de17b79d011dde43ca94a18c7e24950))
* **external-files:** computer_import copies a linked file into her computer ([5677e6c](https://github.com/mikeng-io/nora/commit/5677e6c40880eba89d29904ee2f261a522b16fb1))
* **external-files:** copy a linked file into her computer with file_fetch(to="computer") ([c11ad91](https://github.com/mikeng-io/nora/commit/c11ad9174cbe05ef7bbd5548eeda5a62be3a5447))
* **external-files:** open a shared file link with file_fetch, by its handle's code or the link ([6478f1d](https://github.com/mikeng-io/nora/commit/6478f1dfe9cd1912969572e4f2b3766583793b48))
* **external-files:** read a shared Google Drive file as text, read-only and never cached ([09cb2b5](https://github.com/mikeng-io/nora/commit/09cb2b5ebb4c752b02dbe9f1d24cbf2b60693e83))
* **external-files:** record shared file links as link handles (S1) ([6182341](https://github.com/mikeng-io/nora/commit/6182341df62163ff825be3b851987bfd4b067d31))
* **fire:** the proactive act turn reasons at its own effort, low by default ([d10bd3e](https://github.com/mikeng-io/nora/commit/d10bd3ea4dbba348f993187a4dd4d3cca93e1288))
* **image-gen:** add OpenAI gpt-image-2 through the Codex subscription lane ([59a0b6a](https://github.com/mikeng-io/nora/commit/59a0b6afe2f55b60803a7ff7671fa9c3b87acde7))
* **lessons:** a direct correction wakes the lesson session within ten minutes ([814fd50](https://github.com/mikeng-io/nora/commit/814fd50903b073a57038b32f1ce20c8a98b9a91a))
* **lessons:** a Lessons session per unit writes lessons from closed response episodes ([e0a2b58](https://github.com/mikeng-io/nora/commit/e0a2b588c2020eef54b02a6087304c700d0be832))
* **lessons:** read every lesson against the law before it is written, and keep refusals as tension rows ([10c772e](https://github.com/mikeng-io/nora/commit/10c772ebb2ebc48606050ecc1c540bfa51eaa8d9))
* **lessons:** render a unit's lessons as a block after &lt;self&gt;, behind a flag that ships off ([3b210a2](https://github.com/mikeng-io/nora/commit/3b210a215c794e897709e614a04b43199e3cfefd))
* **lessons:** the lesson store, its write verbs behind five checks, and an operator rollback CLI (L1, DEC-293) ([f077092](https://github.com/mikeng-io/nora/commit/f07709293769052f3b5c2bc18e5bb9676fbbdee4))
* **media:** media_fetch looks at a shared file's picture or video through the source ([33c0123](https://github.com/mikeng-io/nora/commit/33c0123167a2e9351af978b51fa94ed40c6e854e))
* **media:** media_fetch looks at a shared file's picture or video through the source ([4915fb6](https://github.com/mikeng-io/nora/commit/4915fb648854f3e4c13d000e7d6f7bd72b2da935))
* **memory:** project every Record message and attachment into a memory index (S1) ([49fb13a](https://github.com/mikeng-io/nora/commit/49fb13a55266f2c5d1507fc3fe621212e510f02c))
* **memory:** project notes, memos, lessons, events and accounts into the memory index (S2) ([1a9cea4](https://github.com/mikeng-io/nora/commit/1a9cea48e6e0850330185090408b97e9a4dfafcd))
* **memory:** project the current Rollup build's outings, stays and trips (S3) ([399ed84](https://github.com/mikeng-io/nora/commit/399ed847fa250ac73715760dd502b7172539483f))
* **memory:** rebuild the memory index from the stores, identical to live projection (S4) ([d0962b1](https://github.com/mikeng-io/nora/commit/d0962b15aedfa79ebf6fee912b06d3f9a3e391d9))
* **memory:** search the memory index within the reading room's scope, behind a flag (S5, DEC-313) ([b334a6d](https://github.com/mikeng-io/nora/commit/b334a6d5ccf465d9c90e3584f58b5a2ca16cc66b))
* **memos:** keep what a unit settled as a memo, written by Cognition and read through &lt;memos&gt; and memo_read ([50eda51](https://github.com/mikeng-io/nora/commit/50eda5151a1f7024e0b9f7c73fac8da60cb06d4e))
* **prefill:** tell her when a turn is spoken; context-plan step 3 ruled down under deferral ([8d4b327](https://github.com/mikeng-io/nora/commit/8d4b327e67fe4de5bae58755e31e7c1438c30cdf))
* **prefill:** tell Nora when the turn she is answering was spoken ([11fdd53](https://github.com/mikeng-io/nora/commit/11fdd53191d3cef3acdd3860903e1786e47e0c9f))
* **release:** cut releases with release-please on GitHub and retire the upgrade note ([bddf13d](https://github.com/mikeng-io/nora/commit/bddf13d058a0653bc3163f9bb71c9c90a4f9f64e))
* **rollup:** count an unquoted member message as answering her send ([f93a646](https://github.com/mikeng-io/nora/commit/f93a646393eca38bae3612b973d1c8e196390224))
* **self:** render her own account as a self block first in every prompt and retire self_card ([d96199b](https://github.com/mikeng-io/nora/commit/d96199b2e4f674dad3bbd8aa58693ddb11e5f1bb))
* **tools:** let a tag turn read the Rollup's outings (DEC-312) ([dba4f48](https://github.com/mikeng-io/nora/commit/dba4f48a0cddeeb3e4a5e20a99f6f8efb79da5b6))
* **video:** keep each finished clip and send it as a video ([15b0c68](https://github.com/mikeng-io/nora/commit/15b0c68f286cf2d468344c531bc0f35c32b25d08))
* **video:** Nora starts a short video clip and the poller delivers it as a link ([49aa540](https://github.com/mikeng-io/nora/commit/49aa540a1e092658b6003373f99d2e468845dae6))
* **video:** open a clip on the requester's photo with first_frame ([bc0de51](https://github.com/mikeng-io/nora/commit/bc0de51c0df22ab4dc61d8c8999de9c7114c5081))
* **video:** take a clip or a voice note as a video reference ([67238e4](https://github.com/mikeng-io/nora/commit/67238e4770099511c3a778ba9b6c828b53542547))


### Bug Fixes

* **agent:** a heads-up's answer is still owed, so recovery and the fallback still run ([4499369](https://github.com/mikeng-io/nora/commit/44993693424bee4e6ef0205ab450091a0a3296f2))
* **agent:** more covers any work after the reply, and the guard reads it as the tool does (DEC-294) ([47475fe](https://github.com/mikeng-io/nora/commit/47475fe2329ff82a9b1a6b22c9e967f9f000f1be))
* **app:** declare Discord's issuer so Discord sign-in works again ([8242823](https://github.com/mikeng-io/nora/commit/8242823a356f0a3b06428528bbb28b86535b9416))
* **app:** say truthfully where file text is kept ([4d495b6](https://github.com/mikeng-io/nora/commit/4d495b6b06a29cd5cc83e3c5625999916c8a4d62))
* **artifacts:** answer byte ranges so video plays on iPhone, cap the name ([106dc7b](https://github.com/mikeng-io/nora/commit/106dc7b2752c185b7adfc6f05d4519f05c8333b1))
* **character:** a listed person who never posted is not rescanned from the start every tick ([5f83a2d](https://github.com/mikeng-io/nora/commit/5f83a2d1d911c100661f072d748e22145d8ccbbb))
* **character:** a person session carries no web tools ([ed380f3](https://github.com/mikeng-io/nora/commit/ed380f38f76a678d8d8be6c83c133020183834e3))
* **character:** a person session reads its wake batch, not a room's batch limit ([69ca779](https://github.com/mikeng-io/nora/commit/69ca779fdc9cee5747093234ff17588e27be8325))
* **character:** an episode answered only by follows is not evidence for Nora ([ee03f9f](https://github.com/mikeng-io/nora/commit/ee03f9f497aecc7efcc86ec1d334efbed22459a4))
* **character:** bound the rendered block, overlap the tag read, review fixes ([ade1aaa](https://github.com/mikeng-io/nora/commit/ade1aaaf2df6294917e294bb449982d3979838b3))
* **character:** hand no uncited room account to a person or unit session ([549bcd5](https://github.com/mikeng-io/nora/commit/549bcd54cd34623c8cc8ce2db12d9799581c6268))
* **character:** Nora wakes on evidence episodes and cites at least three ([e80c145](https://github.com/mikeng-io/nora/commit/e80c145e871d0b569f47da93a5b80fa05fa70569))
* **character:** render accounts for the people in the turn, capped ([2372986](https://github.com/mikeng-io/nora/commit/2372986922e13495ae7901928472c407c8e55feb))
* **character:** stop the input blocks telling a session to continue the old account ([0d06fb1](https://github.com/mikeng-io/nora/commit/0d06fb18ccdf167e8cac8bd04f93b5dc4c811065))
* **character:** the episode ban is on the text, citations still list them ([3cdb5a6](https://github.com/mikeng-io/nora/commit/3cdb5a6fd3d825efa18d60dce8cc3d7dc92a682a))
* **computer:** keep non-UTF-8 pages in and UTF-16 text out of audio ([957fb0b](https://github.com/mikeng-io/nora/commit/957fb0b967702ffb2f5b5bc1a5a79507411929f6))
* **computer:** the browse client runs off the page's world, waits by loaderId, and asks once ([862ab24](https://github.com/mikeng-io/nora/commit/862ab24527684833bc8f38504482c4a0052fc685))
* **culture:** never hand an uncited previous account to the room pass ([fbf5f55](https://github.com/mikeng-io/nora/commit/fbf5f559fa87911b7cf3b65974a0f4d360fe35cc))
* **deploy:** fail before ssh when LOCK is unset ([36805d3](https://github.com/mikeng-io/nora/commit/36805d33ef3b522d400d139b8bc70c65f12817e3))
* **deploy:** hand the nora container PULSE_API_BASE ([f82a4d6](https://github.com/mikeng-io/nora/commit/f82a4d67d87e644c90f13e939598e729ef227646))
* **deploy:** hold the deploy lock with flock -o so nothing a deploy starts inherits it ([d64a41c](https://github.com/mikeng-io/nora/commit/d64a41cb6356f44aab35a6cbe2ea7d0a60d4175a))
* **deploy:** read the Signal account and the Pulse base from .env, not a scrubbed literal ([7613c03](https://github.com/mikeng-io/nora/commit/7613c033c076dc5663cbda9c016cf2e188c2cab4))
* **doctrine:** a classified part carries a content hash, and the ambiguous three are law (DEC-291) ([6227c24](https://github.com/mikeng-io/nora/commit/6227c24a29c48fcbb08d76317ba6994770a2caa4))
* **doctrine:** act on the Codex and Kimi review of the acting and hand-off lines ([6f5d88e](https://github.com/mikeng-io/nora/commit/6f5d88e77040424ea5d7a2be1970b3878b49f308))
* **doctrine:** ground the location law in the tools, restore voice and DEC-90 text, de-orphan moved lines ([70dfaf0](https://github.com/mikeng-io/nora/commit/70dfaf0d579fa887544795549a82a6133b150960))
* **dreamer:** an unsettled Character session logs a warning, not a stack trace ([764cfac](https://github.com/mikeng-io/nora/commit/764cfacc1cbc8436a9c98f663f0bd43bc646e696))
* **dreamer:** mark a unit that raised as error on its trace root ([fe07854](https://github.com/mikeng-io/nora/commit/fe07854304be5e3f63d8d32c7022bb48ca84e471))
* **engagement:** log after_heads_up from the guard, not from delivered ([82bbd3c](https://github.com/mikeng-io/nora/commit/82bbd3c405f807eb74da15dab28a358749ec2710))
* **engagement:** the tag turn's clock line reads the injected clock ([def0c33](https://github.com/mikeng-io/nora/commit/def0c33bd9b7044f6d6fc981b9669bc19723cfd6))
* **eval:** a snapshot copies the unit's and the listed persons' accounts ([cca0629](https://github.com/mikeng-io/nora/commit/cca062983389a12f232ce9791e0bade8c0885f69))
* **evals:** a proactive case row replaces a seeded copy that names no note ([ce83614](https://github.com/mikeng-io/nora/commit/ce836145b49e1f222bfc39a2f8ec99ff9300dfc3))
* **evals:** a sandbox Signal send lands instead of answering refused ([921756d](https://github.com/mikeng-io/nora/commit/921756dac2019483c3bbe2b08fbfdd7b0c83c33b))
* **evals:** a text must_cite grades the message, not a silence the case allows ([0a61144](https://github.com/mikeng-io/nora/commit/0a61144c2689470dd984a6c357d9977e3942e5bf))
* **evals:** check what llama-server would embed for WeMM, and run it at fp32 through HF ([3a4a3fc](https://github.com/mikeng-io/nora/commit/3a4a3fc1143dacd1060c90904d604f45f338ee87))
* **evals:** check what the HF adapter loaded, and stop the watchdog on low free swap ([cab3325](https://github.com/mikeng-io/nora/commit/cab33251fc5e2ce6556aa952480b765b289a023f))
* **evals:** close the bake-off's fail-open paths and adopt the halve-the-misses rule ([60a9405](https://github.com/mikeng-io/nora/commit/60a940523a5b074574662d1d942af89c4b8b133f))
* **evals:** either counts a turn that decided on silence as an answer ([a78a899](https://github.com/mikeng-io/nora/commit/a78a8991fa727bf1291c91b1ed8f50071073288c))
* **evals:** feed WeMM-2B its own input format through llama-server ([0b9c3bd](https://github.com/mikeng-io/nora/commit/0b9c3bda3e5f900e5b46e7479ac73342d8368071))
* **evals:** give Qwen3-VL-Embedding queries the vendor's retrieval instruction, and test the loader ([4dfe55a](https://github.com/mikeng-io/nora/commit/4dfe55aeb9d8730635d5f0f246dd0d1d2ac87bd3))
* **evals:** load each bake-off slice from the files set.yaml names for it ([fdff384](https://github.com/mikeng-io/nora/commit/fdff384fe1d2d6522759191c3ced0650350d1163))
* **evals:** make the recall corpus judge-only and pin its evidence wiring, per review ([81b891d](https://github.com/mikeng-io/nora/commit/81b891d358693b8ac7fba0278a0ff4f8c6dac4e4))
* **evals:** match WeMM's tokenizer on every added token, and re-derive both verdicts from the files ([d7ae7c2](https://github.com/mikeng-io/nora/commit/d7ae7c268cd69843086995eb8bd8d9bd318e963c))
* **evals:** name the blind lessons cases and refuse seeding over held lessons ([478a948](https://github.com/mikeng-io/nora/commit/478a9482a49b64477fdbb00758822772ed17d543))
* **evals:** never read a self-closed block as an opening tag ([591dd4e](https://github.com/mikeng-io/nora/commit/591dd4ec74937e259f0c9b9570c051461432bb64))
* **evals:** only a turn that ran counts as an either answer, not a screened row ([101552b](https://github.com/mikeng-io/nora/commit/101552ba3df6d95ec37b8721b561fc375bcf28d3))
* **evals:** refuse a candidate arm measured twice, and load the first Qwen run as qwen3vl-defaultinst ([de047b0](https://github.com/mikeng-io/nora/commit/de047b048e0be02959d30b9b6fe04a87c17e9c30))
* **evals:** resolve the scrubbed corpus anchors through a private map on the deploy host ([89b690f](https://github.com/mikeng-io/nora/commit/89b690f71b6d3588d7f4a0b69225f91e944263b7))
* **evals:** rewrite the bake-off's text-to-image queries and labels blind ([1ab2328](https://github.com/mikeng-io/nora/commit/1ab232874573bd7612934f5169b01c5f7445ad9d))
* **evals:** seed entity-anchored notes, and let a case row replace its seeded copy ([28894a5](https://github.com/mikeng-io/nora/commit/28894a513ebbfe20003b2da53dbdc9e0021baae3))
* **evals:** settle waits on the tasks a turn spawned, not on a count ([b34d34e](https://github.com/mikeng-io/nora/commit/b34d34e15916d3ea8e5ac380ffc1dee7e14ce0a1))
* **evals:** show the recall judge every context block her turn was shown ([ba17dcd](https://github.com/mikeng-io/nora/commit/ba17dcdd98f5629cb979eaf6ec9e48a3085c626c))
* **evals:** translate names glued to Cantonese, withhold the real room in a refusal ([936766e](https://github.com/mikeng-io/nora/commit/936766efe76fafe236bf221bfcc2d76366b81344))
* **evals:** write an array setting as an array in the eval subject config ([6e37873](https://github.com/mikeng-io/nora/commit/6e378732f1ac8d160a96e2db0a1fc8693b1558f4))
* **external-files:** a link she builds around a handle's code is that code ([25a48a8](https://github.com/mikeng-io/nora/commit/25a48a82809212e35060141b2fe746acc88cf3ce))
* **external-files:** a link she builds around a handle's code is that code ([c6c0b25](https://github.com/mikeng-io/nora/commit/c6c0b25ee5076301d058737a4a17d4871c0311bc))
* **external-files:** close the raw-link paths the PR 795 review found ([08849b6](https://github.com/mikeng-io/nora/commit/08849b62a16524b83a1dc84a4c2f64b54105040a))
* **external-files:** keep a trailing id underscore, unwrap nested links, split glued ones ([cc1fdbc](https://github.com/mikeng-io/nora/commit/cc1fdbc2b07dbf8300f8d8a37d38e246bb34e815))
* **external-files:** keep the raw code out of computer_import's audit row ([915bf76](https://github.com/mikeng-io/nora/commit/915bf7614b1e52637cabdf120db72f41977ce066))
* **external-files:** parse in a killable child, stream OOXML once, refuse bzip2/LZMA members ([0a66209](https://github.com/mikeng-io/nora/commit/0a66209496ec2e967f47a8e2842d5346c24139b3))
* **external-files:** read a made-up link's code only from its id slot, one argument at a time ([3e5ecab](https://github.com/mikeng-io/nora/commit/3e5ecabcafb210e876690ab2a121063855cadd19))
* **external-files:** read-only in the credential, row lines charged, OOM is too_large ([e23b3e1](https://github.com/mikeng-io/nora/commit/e23b3e1b38aa02521bde3a73437356b78c00953a))
* **external-files:** refuse a compressed body cut off mid-stream ([79cb347](https://github.com/mikeng-io/nora/commit/79cb347892787c39b2d2abca078b0e9eba48e154))
* **file-fetch:** clamp the name, render links in the text, claim file links only when wired ([2531981](https://github.com/mikeng-io/nora/commit/25319812fde8247bb2ba8cadf7829e0a99bb7aa1))
* **fire:** carry the room's location trust into a fire turn's where_is ([e7b61ed](https://github.com/mikeng-io/nora/commit/e7b61edefdec7846b9df895d5aecd95f567e6aeb))
* **fire:** keep a shadow-held participation turn strict, and correct the comments ([52cef62](https://github.com/mikeng-io/nora/commit/52cef62ee0d63f3c139ce6a1906686be4c3ce092))
* **fire:** the proactive turn has its own per-call output ceiling ([6351968](https://github.com/mikeng-io/nora/commit/6351968a57ad91fdcc62fc126ce45fb7cd026fcf))
* **google:** read a gzipped token answer, and ask for an uncompressed one ([07ca39e](https://github.com/mikeng-io/nora/commit/07ca39e1e43ca29d0129abce471de01f8cce0fbe))
* **guards:** refuse a reply that carries the prompt's transcript markup ([7e17859](https://github.com/mikeng-io/nora/commit/7e17859ca9b58ef9199fc511e8688401cbed8c4c))
* **guards:** refuse a reply that carries the prompt's transcript markup ([a7fb65f](https://github.com/mikeng-io/nora/commit/a7fb65ffa9d393b349609924889b60ff0fd8c8cf))
* **hooks:** blank project identity before denylist matching ([0ad69e0](https://github.com/mikeng-io/nora/commit/0ad69e07d9480c0fb1814e687673f02d65cc6e60))
* **hooks:** never blank the bare stem or another mailbox at the domain ([a1fd287](https://github.com/mikeng-io/nora/commit/a1fd2876d0f50340ade31fb923a90461a578763c))
* **image_gen:** adopt a pre-fingerprint state file, falling back to the login ([3db23e7](https://github.com/mikeng-io/nora/commit/3db23e73baaa1a168fa3de4e59d12eb93d6d0c16))
* **image_gen:** close the keepalive review's findings ([93ea159](https://github.com/mikeng-io/nora/commit/93ea159a90fb8c911fcfd15ef6263770d921ab4f))
* **image_gen:** keep both OAuth logins alive on a schedule, and loud ([2c5ed19](https://github.com/mikeng-io/nora/commit/2c5ed1927a3dd0b29db67d6e7f41e6373ff19da3))
* **image-gen:** count a repeated reference once and refuse an empty one ([c771cf8](https://github.com/mikeng-io/nora/commit/c771cf86b3895f10c237afcb47a0ee179b0f7c34))
* **image-gen:** keep the Codex lane's rotated token through failed writes and cancellation ([ae6d1f5](https://github.com/mikeng-io/nora/commit/ae6d1f5538c83b011a1b355c9a5be99064cd841c))
* **lessons:** a head that moved is a conflict, the operator can floor a session-retired lesson, and a lesson needs 12 letters ([#756](https://github.com/mikeng-io/nora/issues/756) review) ([6de9bec](https://github.com/mikeng-io/nora/commit/6de9bec2b773a104f53be437d4896a4be6872a08))
* **lessons:** count an @-mention as addressing her, read a curly don't, and name her only as a word ([c669558](https://github.com/mikeng-io/nora/commit/c669558116e4445be4f97e4c5755048e2c5b008d))
* **lessons:** escape the lesson into the judge, read doubt as conflict, and use the raw JSON client ([1aaf98a](https://github.com/mikeng-io/nora/commit/1aaf98abf47853f9c92ce369c0309a29c9ce6865))
* **lessons:** name a held lesson by handle in refusals, and hold back a batch too short to cite ([290d348](https://github.com/mikeng-io/nora/commit/290d348ea65fe67c476a9c272e922747f2934cfb))
* **lessons:** seed eval lessons a millisecond apart so their order is stable ([b9f0c17](https://github.com/mikeng-io/nora/commit/b9f0c17773e3b9117c282da44125bf78c8e2c453))
* **links:** treat a percent-decoded Maps URL as present, name the blob store ([8763841](https://github.com/mikeng-io/nora/commit/87638418bd02f124260cad6d891a36761a06d79d))
* **media:** close the review's gaps in media_fetch's shared-file path ([0fd7ab6](https://github.com/mikeng-io/nora/commit/0fd7ab65fd99cb0b37d48632e49a3435d692e5d2))
* **memory:** hold a hub while attaching to it, and drop edgeless hubs on every path ([d20fe4f](https://github.com/mikeng-io/nora/commit/d20fe4f5219e355225c9740a0d60102c1dcc7cbb))
* **memory:** index a person account only under a proven unit, and lock the event on RSVP ([aef5f27](https://github.com/mikeng-io/nora/commit/aef5f27459989071ab1c49461f28e3fe7c9d0484))
* **memory:** keep a queued episode through a concurrent build switch, and drain fairly ([e2104df](https://github.com/mikeng-io/nora/commit/e2104df0dfd1736abfd819bef23bde5021037698))
* **memory:** queue any number of failed ids, clear a queue row on success, tighten the guard ([0759aad](https://github.com/mikeng-io/nora/commit/0759aadcee2ff39d8e9aa92b3db5e4ddc14728d0))
* **memory:** screen identifiers before truncation and keep every search failure fail-closed ([25c779d](https://github.com/mikeng-io/nora/commit/25c779df7e8dd4754c20a89e7500e21b7fff14e1))
* **memos:** bump conduct's version and cover memo_read and the memo tables where the suite enumerates them ([67bb267](https://github.com/mikeng-io/nora/commit/67bb2679c22331f3047be35d4899208ad24c51b5))
* **memos:** make the keep promise true and timeless, keep values out of titles, cap &lt;memos&gt;, and close the review's gaps ([3c7785b](https://github.com/mikeng-io/nora/commit/3c7785b56601de265b012c49e534bbea4d1f7421))
* **notes:** complete a missing or first-name attribution from the cited senders ([6ef83ed](https://github.com/mikeng-io/nora/commit/6ef83edd8e1955d714be7bdd829a8a234327bca8))
* **notes:** complete names only when one person can have said the line ([38a8078](https://github.com/mikeng-io/nora/commit/38a807870028c0ad1cf8e9dd23142bbee555b6a2))
* **prompt:** #n and reply_to index the shown rows, who_said alone gets the deeper read ([6ee0747](https://github.com/mikeng-io/nora/commit/6ee0747b67369b6c4fcf198ae4d8e5add7e23cca))
* **rollup:** a quote of another member's message is not a follow ([88511d1](https://github.com/mikeng-io/nora/commit/88511d1d8ba8d5b532cda6e0c2cbc281542b5d1c))
* **scrub:** the ordinary word stays when a member's surname is also that word ([a532d48](https://github.com/mikeng-io/nora/commit/a532d4843cd02022876bd721e97614e3cf4a4360))
* **self:** cap the self block on characters and keep a location room's sends out of her account ([7d7ecf9](https://github.com/mikeng-io/nora/commit/7d7ecf983b8183282c6cbd68ca60c2cb00b6f3a5))
* **self:** render her unit-wide account only in rooms that may hold it (R-31) ([44108ce](https://github.com/mikeng-io/nora/commit/44108ce00cf8b24c26c104ec5e43a2a199473d15))
* **skills:** keep the video prompt rules inside what the tool can do ([3b045a7](https://github.com/mikeng-io/nora/commit/3b045a7a90cdc9a89634402eb95fc719ddc8fcfc))
* **skills:** tell Nora her computer reaches the public web, not nothing ([1ea4cf1](https://github.com/mikeng-io/nora/commit/1ea4cf16443233112d692093120219aec6b4c4c5))
* **skills:** write video prompts the way MiniMax H3 reads them ([434b407](https://github.com/mikeng-io/nora/commit/434b407c7592d82e22626236a2b027bd1c0d16e4))
* **snapshot:** restore the backfill a migration dropped, so a full-restore case runs ([f13fe20](https://github.com/mikeng-io/nora/commit/f13fe20fe5345e2b080800874d6e3145150e1e97))
* **social:** name Nora's OpenCLI profile on every read, never the default ([ce3b277](https://github.com/mikeng-io/nora/commit/ce3b2778672b79bd935cf362469346c1aa6ffc02))
* **social:** refuse a blank OpenCLI profile in the probe too, and document it ([e729404](https://github.com/mikeng-io/nora/commit/e729404f613f792af6b72eb3b6aabae5a3c4a2de))
* **tools:** media_fetch points a file link handle at file_fetch too ([3ec2b8e](https://github.com/mikeng-io/nora/commit/3ec2b8e8e71f528d0726c755223796ad67fcdb5b))
* **tools:** the now tool reads the turn's injected clock, as the clock line does ([148dae1](https://github.com/mikeng-io/nora/commit/148dae11364d878ce78745d1e48cca7ddf966e82))
* **tools:** web_fetch points a file link handle at file_fetch ([72f383f](https://github.com/mikeng-io/nora/commit/72f383f14d460617212287334f9167e68507c78a))
* **tools:** web_fetch points a file link handle at file_fetch ([97a46de](https://github.com/mikeng-io/nora/commit/97a46de5c02024f266ad63cef95f4dc8be961fde))
* **video:** answer a room at its cap before encoding its references ([be1256e](https://github.com/mikeng-io/nora/commit/be1256eabff2e0ad1ee4a1508f9bb192d82b4bc2))
* **video:** keep a 2 s share at the floor and read a WebM picture length ([336406f](https://github.com/mikeng-io/nora/commit/336406f474722eaac9d18d751f3bc87929b4f031))
* **video:** keep a clip silent unless audio may leave, and fit inside every limit ([de2147e](https://github.com/mikeng-io/nora/commit/de2147ee75a91a47235b0efe9436f5cf1e9c5f27))
* **video:** let the words-only call run after a refused reference ([3354b5c](https://github.com/mikeng-io/nora/commit/3354b5c4e5abf521b14de45e9f590e6be40fa595))
* **video:** treat an accepted create with no task id as uncertain ([7ca9537](https://github.com/mikeng-io/nora/commit/7ca95377b99118cecca3ae432c1721d7e09c83a8))


### Security

* **culture:** the room fold no longer reads the room's standing, so no presence line reaches unit-wide accounts ([67482d0](https://github.com/mikeng-io/nora/commit/67482d0ee61cbc2d062f536e6b9992ecf3f74ecd))
* **external-files:** bound every read of a shared file while it is read, not after ([47e6cb0](https://github.com/mikeng-io/nora/commit/47e6cb049c4009b321f50c2ed3745d88d00a74d0))
* **hooks:** the identifier hooks decide by rule, never by a model ([6b11c3b](https://github.com/mikeng-io/nora/commit/6b11c3b1d2c6a64fa17cabcff773eb56fe7becd7))
* **scrub:** move test coordinates that sat on real dwell places ([13c8999](https://github.com/mikeng-io/nora/commit/13c8999052c63fc61e061ccef17e82a458b05e1d))
* **scrub:** replace real identifiers that came back after the clean-forward ([a4f6a3e](https://github.com/mikeng-io/nora/commit/a4f6a3e55f1dae980614b50742d9b5083fc3dc2e))
* **scrub:** replace the street names that sat beside person-linked coordinates ([3b08d84](https://github.com/mikeng-io/nora/commit/3b08d8418cf890e269bf96f6c7e718d719615646))
* **scrub:** the tree carries no real identifier, fixed forward before the history rewrite ([27cacae](https://github.com/mikeng-io/nora/commit/27cacaed6b6d43ed3440fb673e5e91aebef9ad17))


### Changes

* **agent:** a heads-up is her own flag on send_reply, not inferred from more ([7645ff3](https://github.com/mikeng-io/nora/commit/7645ff30e36cd1fe3b106af7b85e76eaa9af7576))
* **agent:** a send_reply ends the turn unless it carries more=true (DEC-293) ([6311580](https://github.com/mikeng-io/nora/commit/6311580b2a83a96f2a8d9f7ca31fa83dc459799d))
* **character:** Nora writes her own account without retelling single episodes ([c61088d](https://github.com/mikeng-io/nora/commit/c61088d87398b55b1db2d9945bc81ea35061f8f9))
* **character:** write accounts as short syntheses of a few traits, re-judged each time ([7adfd72](https://github.com/mikeng-io/nora/commit/7adfd725958dad1ae0fe4b5d4c1ce4245d34724b))
* **dreamer:** open each unit as its own trace, the pass as its session ([7e30024](https://github.com/mikeng-io/nora/commit/7e30024d511210a1f5409d67b8ddb47c5cabd10f))
* **events:** an event owns no reminder; the daily floors bring Nora the coming ones ([888beb1](https://github.com/mikeng-io/nora/commit/888beb12010ca7722791b5f3154c8caae04fa8cb))
* **image-gen:** follow the active provider's reference image limit ([770f769](https://github.com/mikeng-io/nora/commit/770f769f638c8b830dde8d4f8e4f2e7141d955a5))
* **lessons:** a lesson goes ahead of the doctrine's practice, never its law (DEC-311) ([aebe442](https://github.com/mikeng-io/nora/commit/aebe442758400cb95bab48e096fb9ee73a051dfc))
* **lessons:** a lesson goes ahead of the doctrine's practice, never its law (DEC-311) ([1f6c575](https://github.com/mikeng-io/nora/commit/1f6c5754c5207d3f171aaab59cf0c8015bfe0678))
* **participation:** drop the silence base rate from the participation header ([0382555](https://github.com/mikeng-io/nora/commit/0382555fc68d89f16373ebb7ca3ff3c8f6253a89))
* **prompt:** show the tag turn 20 transcript rows, keep the 40-row read for lookups ([c9d0e05](https://github.com/mikeng-io/nora/commit/c9d0e05c96820b72fad818bd20504a617205c24d))
* **self:** drop the location-off-only evidence filter on her account ([27a0315](https://github.com/mikeng-io/nora/commit/27a03159ef2764616fc712ab8a76d9237265b874))
* **video:** count the video_create daily cap per room, rename the key ([1915aab](https://github.com/mikeng-io/nora/commit/1915aab719855e88077a6a38aae6abd5db293894))


### Doctrine

* **classification:** rule the sort's eleven AMBIGUOUS rows and add an identity seed (DEC-309) ([a11c348](https://github.com/mikeng-io/nora/commit/a11c348e1bf792c06802e1bcc55b64c84b527aef))
* **conduct:** say what you are doing first when the work will keep the room waiting ([0c2c760](https://github.com/mikeng-io/nora/commit/0c2c760a444233255da767ded0db8e1bacc977bd))
* **conduct:** take back the line moved from silence.md (v41) ([8a7d519](https://github.com/mikeng-io/nora/commit/8a7d5195f36c8e0cf1a9e29738a5334027858cc4))
* dissolve silence.md into the turn headers and conduct ([06d2d24](https://github.com/mikeng-io/nora/commit/06d2d24ea8c332ac7db9fa03118477e3d7be2c8f))
* dissolve silence.md into the turn headers and conduct ([1bffd20](https://github.com/mikeng-io/nora/commit/1bffd20333002f48c98c29dd04785cffd940b00b))
* **identity:** say a work place is not the closed machine, that she acts, and when to hand work off ([54c8d4e](https://github.com/mikeng-io/nora/commit/54c8d4e6ccecbc8fcd2665bef6acca1291e27be3))
* **seed:** voice, belonging and conduct character is seed, not grown ([9c09fe6](https://github.com/mikeng-io/nora/commit/9c09fe612829e9af7c62fc69f5b1a7c716d106a9))
* **seed:** voice, belonging and conduct character is seed, not grown ([0ca09b0](https://github.com/mikeng-io/nora/commit/0ca09b0f8d36a6309587601cb3c0c14f04bd49d7))
* sort every rule into law, practice or character with no text change (DEC-291) ([bbab6c9](https://github.com/mikeng-io/nora/commit/bbab6c99afdf2ab687634b9d169c5cb5eb794908))
* **sort:** mark the identity seed as its own bucket, never cut ([fda9cb3](https://github.com/mikeng-io/nora/commit/fda9cb3966b7de72bf4a9b5273a43db762789045))
* **summons:** the turn states its reply limit; the number leaves public/conduct.md ([bd1275d](https://github.com/mikeng-io/nora/commit/bd1275d5e76f9424c4e5887c6da49f8342f0d9ee))
* **summons:** the turn states its reply limit; the number leaves public/conduct.md ([4894624](https://github.com/mikeng-io/nora/commit/4894624ffa867237bfcb1a85c34ebf81e9f16b6d))
