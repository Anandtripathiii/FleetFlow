# Policy Arbiter evaluation

Average queue wait in seconds over seeds [0, 1, 2, 3, 4, 5, 6, 7, 8, 9] (lower is better). Horizon 180s, margin 5%, rollouts 3, runtime 39s.

|Scenario|Rules|RL|Arbiter|Check|Result|Decisions rl / rules / none|
|-|-:|-:|-:|-|-|-|
|normal|47.1|25.1|**21.8**|arbiter within 5% of RL|PASS|130 / 538 / 832|
|spike|334.5|256.3|**256.4**|arbiter within 5% of RL|PASS|88 / 50 / 1362|
|moving|319.2|263.9|**251.9**|arbiter within 5% of RL|PASS|88 / 45 / 1367|
|rush|231.9|154.4|**134.6**|arbiter within 5% of RL|PASS|63 / 45 / 1392|
|failure|376.7|321.8|**321.6**|arbiter within 5% of RL|PASS|80 / 49 / 1371|
|mixed|235.7|252.5|**91.2**|arbiter <= rules|PASS|53 / 305 / 1142|

All decisions: rl 502, rules 1032, none 7466.
