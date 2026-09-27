# 学术会议同行评审系统

一个仅使用 Python 3.11+ 标准库的独立示例项目。SQLite 保存数据，`http.server` 提供 JSON API 和演示页面。

## 运行

```bash
python app.py --init --seed
python app.py
```

访问 <http://127.0.0.1:8101>。默认数据库为 `review.db`，端口为 `8101`。测试：

```bash
python -m unittest -v
```

## 角色和主要接口

演示用户：`alice`、`bob`（作者），`r1`、`r2`、`r3`（评审人），`chair`（主席）。所有 API 请求应带 `X-User-Id` 请求头。

- `POST /api/papers`：提交论文。
- `GET /api/papers` / `GET /api/papers/{id}`：按角色隔离查看；评审人看到双盲视图。
- `POST /api/papers/{id}/bids`：评审意向。
- `POST /api/papers/{id}/conflicts`：主席登记利益冲突。
- `POST /api/papers/{id}/assignments`：主席邀请评审人，执行负载上限与冲突检查。
- `POST /api/assignments/{id}/respond`：接受或拒绝邀请。
- `POST /api/assignments/{id}/review`：提交 1-5 分评审。
- `POST /api/papers/{id}/rebuttal`：作者提交一次 Rebuttal。
- `POST /api/papers/{id}/rebuttal-requests`：主席请已完成评审的评审人补充说明（答辩回应）。
- `GET /api/papers/{id}/rebuttal-requests`：查看某论文的请求与回应；评审人只能看到自己的。
- `GET /api/rebuttal-requests`：评审人查看自己的补充说明请求。
- `POST /api/rebuttal-requests/{id}/respond`：评审人本人填写补充说明。
- `POST /api/papers/{id}/decision`：收到至少两份评审后作决定；仍有未回应的补充说明请求时不能决定。
- `GET /api/papers/{id}/history`：审计历史。

## 业务不变量

评审人不能查看未分配论文的作者身份；利益冲突禁止投标和分配；邀请和完成状态不能跳步；每位评审人的未完成分配受 `load_limit` 限制；每篇论文只能提交一次 Rebuttal；决定必须至少基于两份已完成评审。主席可请已评完的评审人补充说明：补充说明单独记录，不改动原评分与意见，原评审仍计入最低份数；请求未全部回应前不能作决定；重复请求、已有决定后再请求、他人代替提交都会被拒绝。
