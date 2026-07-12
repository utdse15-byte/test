# FP security loop — untrusted-archive decompression limits (完成报告)

日期:2026-07-12 · 执行者:编排者本人(独立小闭环,与并行 agent 文件不相交)
范围:roadmap §8.9「untrusted archive limits / zip bomb guard」+ §15.8 fixture
子集。审计 → 红灯 → 最小修复 → 回归,一次窄闭环。

## 审计(现状)

- 成员名越界防护已存在且有桩:`_ZIP_UNSAFE`(seriespack.py)拒绝绝对路径/
  盘符/`..`(test_c17_packs.py 定桩 `/etc/passwd`、`../outside.md`);
  `manju unpack` 已拒绝 symlink 成员、越界成员名,且不信任 zip comment 路径
  (Round Y/goal-20)。**未重复建设。**
- 缺口一(真实):`inspect_template_pack` / `import_template_pack` 对每个成员
  `zf.read()` 全量解压进内存,**无任何解压上限** — 只读 inspect 恰是用户对
  不可信 pack 的第一步操作,zip bomb 直接打内存。
- 缺口二(真实):`manju unpack` 在成员校验后直接 `extractall`,**无解压体量
  预检** — 声明超大体量的炸弹包填满磁盘。

## 设计裁定(两面两策,拒绝投机启发式)

- template pack 按契约就是小包(bible 条目 + 少量 ref 媒体)⇒ 硬绝对上限:
  `MAX_PACK_MEMBERS=256`、`MAX_PACK_MEMBER_BYTES=64MiB`、
  `MAX_PACK_TOTAL_BYTES=256MiB`(声明值预检)+ `_read_member_capped`
  流式截断读(**不信任 central directory 声明大小** — 伪造小声明也在流层
  被截断拒绝)。
- 项目恢复(unpack)合法体量可以很大,且数字静音 WAV 等媒体可合法达到
  ~1000:1 压缩比 ⇒ 绝对上限/压缩比启发式都会误伤真实项目。诚实防护 =
  真实危害向量本身:成员数 sanity 上限(100_000)+ **声明解压总量 vs 目标
  文件系统实际剩余空间**(留 64MiB 余量)预检,`extractall` 之前结构化拒绝。

## 红灯 → 绿灯(tests/test_fp_security.py,9 条)

| ID | 基线 | 现在 |
|----|------|------|
| S1 声明超限成员拒绝 | FAIL(无 API/无上限) | inspect 预检拒绝 |
| S2 声明总量超限拒绝 | FAIL | 同上 |
| S3 成员数超限拒绝 | FAIL | 同上 |
| S4 伪造声明防御(流式截断) | FAIL | `_read_member_capped` 拒绝 |
| S5 正常小包不受影响 | (守护) | inspect ok=True + 正常导入 |
| S6 symlink 成员绝不落地为链接 | (行为桩) | 按构造中和(字节内容寻址落盘) |
| S7 unpack 声明总量 > 磁盘可用即拒 | FAIL(直接 extractall) | 预检拒绝,dest 不产生 |
| S8 unpack 成员数上限 | FAIL | 预检拒绝 |
| S9 正常 .manjupkg 恢复不受影响 | (守护) | 端到端通过 |

## 修改文件

- `src/manju/build/seriespack.py` — 三个上限常量 + `_check_pack_limits` +
  `_read_member_capped`;inspect/import 全部 6 处成员读取改走截断读。
- `src/manju/cli.py` — `UNPACK_MAX_MEMBERS`/`UNPACK_FREE_DISK_MARGIN_BYTES`
  常量 + unpack 解压前体量预检(声明总量 vs `shutil.disk_usage` 实际剩余)。
- `tests/test_fp_security.py` — 新增(上表)。

## 回归

- tests/test_fp_security.py + tests/test_c17_packs.py:24 passed。
- tests/test_cli.py -k "pack or unpack or archive":2 passed(既有往返桩)。
- 既有测试修改:0。新公共 Schema:0。新依赖:0。网络:0。
- 全量回归随 wave-1 提交统一执行(与并行 agent 的 F0/timebase 产物同批)。
