# dsh-client-ui-bgstop

DSH Web GUI 的「停止后台任务」按钮插件。

会话头部新增「停止后台任务」按钮：仅在存在运行中后台任务（如 Python 后台任务）时出现，
点开面板可列出所有运行中的后台任务（跨会话可见，标注来源会话），逐条停止或全部停止。
数据经 `/api/dsh-bgstop` 路由（loopback 围栏）读取 `jobs` 注册表，停止时以任务归属会话
的 agent 作为 caller 执行 `jobs.kill`，权限由任务注册表校验。

- 目录：`packages/dsh-client-ui-bgstop`
- 安装：经聚合包 `dsh-web-ui-all` 一键安装（`aggregate.yml` 已收录），或单独挂载
  （`cordis.patch.yml` insert 行 + profile node_modules）
- 构建：`pnpm --filter @linxin666/dsh-client-ui-bgstop build`
