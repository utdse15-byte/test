# Skill eval cases

这些案例评估导演判断，不比较文学措辞，也不调用 provider。每个 YAML 都是 `manju.skill-eval/v1`，可用 `yaml.safe_load` 解析；`prompt` 是给 agent 的输入，`expected.must` 是结构化复核必须出现的判断，`expected.must_not` 是不可出现的越权，`review` 说明人工/agent 如何判定通过。

退出标准要求十个最低案例全部通过结构化复核。案例只能引用已落地的 SceneContract、ShotContract、媒体观察、ProviderManifest 和 production readiness 语义；不得用 caption-card、伪造媒体或虚构 provider 证据代替真实判断。
