# 后续发布验收与继承档案

## 不能再弄错的版本事实

原始可恢复基线是 R2 13a2ab12dac109bece5a2be63254024c11ebc390。
原版 R3/R4 ZIP 没有找回，本轮重建不得冒称字节恢复。
本轮 R3=93faea0fec9b015a6d86ae5807d1da196b390026；R4=2177850424ae324a65a0e66a6ca04479cacb813d；R5=fc253b2198b22d9efefadf8df1aaba9ed75d5519；R6=d63c3db7f6073a9f070431e4b60eaa259d78c004。
R7 的精确提交由交付根目录 PACKAGE.json 给出，tag为 delivery/r7；必须在包内核验源码/wheel/Git，不从聊天推断文件存在。
所有阶段ZIP分别保留，不覆盖。用户只需安装累计最新版本，旧阶段主要作为恢复点。

## 已验证与未验证严格分开

R7核心分组共382个不重复测试点，381通过、1跳过。源码与安装版250个运行时文件相同；使用宿主依赖，不是全新Windows成功安装。
R2保存和首次添加保护、供应商计费/取消边界、CLI和协议登记都独立复验。
一次同进程组合运行在120秒被外部结束，没有完整JUnit。另有R6固定快照的大范围模块回归，封存时420模块有记录，2模块主动中止、71未开始；精确红项见包内EVIDENCE/r6-campaign-summary.json，不能叫完整全绿。
测试顺序导致组合运行卡住的根因尚未证实，不要用放宽超时、删除测试或给产品加吞异常补丁强行变绿。
Windows进程树、原生file://导航/双击、完整安装、更新回滚、固定FFmpeg6.1.1均未实机验收。真正Chromium启动后的取消已在Linux观察验证，不等于正常截图渲染通过。
真实商用生成调用为0，模型约束档不等于API连接。画质/速度/费用/远端取消均无实测排名。

## 已做出的接口取舍

模型Request/v1的规范化格式保持旧黄金夹具哈希，来源上下文不塞入请求本体。
R6新定稿bundle/v2必须有实际草稿视频和审片链；v1继续读，旧final声明明确不是晋升证明。
审片记录是本地声明，不是身份签名，不是联网撤销服务，也不能自动写入 selected_take 或Picture Lock。
模型工作台通过新标签和只读镜头任务下载接回原GUI；不自动导入并覆盖旧草稿。
安装器默认无网络；Windows入口通过明确的INSTALL确认允许下载依赖。只创建私有新环境，失败只清理它，不覆盖旧版。

## 下一轮最高价值验收

先确认用户保存到了R7文件，并在新目录测试HTML工作台和完整应用；不要开始新型号前再次丢失交付基线。
Windows真实环境按安装/启动/退出/取消/旧片保护/回滚执行；保留原用户工程，用副本验收。
隔离复现组合测试的顺序问题和旧红项，分别对照R2与R7源码，不能全归咎于Linux或沙箱。
新供应商执行必须先有job ID账本、超时/取消/远端计费边界及零费用保护，再谈付费试片，不能用本地取消冒称远端已停止。

## 证据索引

REPORTS/rebuilt-delivery/R3.md至R7.md；RESEARCH.md。
EVIDENCE/r7-grouped-acceptance.json、r7-runtime-consistency.json、r7-installed-review/RESULT.json、r7-installed-gui-final/RESULT.json、r7-real-chromium-cancel/RESULT.json。
下载包顶层VERIFY_PACKAGE.py、ZIP_CHECK.py、VERIFICATION.json、SHA256SUMS.json。
